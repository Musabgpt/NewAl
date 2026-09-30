"""Reads a GGUF file's metadata (no dependencies): architecture, context length, layers and attention shape, so the
memory a model needs (weights + KV cache per token of context) is known for any GGUF file the user adds."""

import os
import struct

MAGIC = b"GGUF"
# GGUF value types
_FMT = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_STRING, _ARRAY = 8, 9


class _Reader:
    def __init__(self, f):
        self.f = f

    def take(self, fmt):
        size = struct.calcsize(fmt)
        data = self.f.read(size)
        if len(data) != size:
            raise ValueError("truncated GGUF header")
        return struct.unpack(fmt, data)[0]

    def string(self, limit=1 << 20):
        n = self.take("<Q")
        if n > limit:
            self.f.seek(n, os.SEEK_CUR)          # long values (a chat template, a vocabulary) are skipped
            return None
        return self.f.read(n).decode("utf-8", "replace")

    def value(self, vtype, keep_arrays):
        if vtype in _FMT:
            return self.take(_FMT[vtype])
        if vtype == _STRING:
            return self.string()
        if vtype == _ARRAY:
            itype = self.take("<I")
            n = self.take("<Q")
            if not keep_arrays or n > 4096:
                # skip: fixed-size items in one seek, strings one by one
                if itype in _FMT:
                    self.f.seek(n * struct.calcsize(_FMT[itype]), os.SEEK_CUR)
                elif itype == _STRING:
                    for _ in range(n):
                        self.f.seek(self.take("<Q"), os.SEEK_CUR)
                else:
                    for _ in range(n):
                        self.value(itype, False)
                return None
            return [self.value(itype, False) for _ in range(n)]
        raise ValueError("unknown GGUF value type %d" % vtype)


def metadata(path, keep_template=False):
    """The file's key/value metadata (tokenizer arrays are skipped, and the chat template unless asked)."""
    out = {}
    with open(path, "rb") as f:
        if f.read(4) != MAGIC:
            raise ValueError("not a GGUF file: %s" % path)
        r = _Reader(f)
        version = r.take("<I")
        if version < 2:
            raise ValueError("GGUF version %d is too old" % version)
        r.take("<Q")                     # tensor count
        n_kv = r.take("<Q")
        for _ in range(n_kv):
            key = r.string()
            vtype = r.take("<I")
            keep = not key.startswith("tokenizer.ggml.")
            if key == "tokenizer.chat_template" and not keep_template:
                if vtype == _STRING:
                    f.seek(r.take("<Q"), os.SEEK_CUR)
                    continue
            out[key] = r.value(vtype, keep)
    return out


_infos = {}


def info(path):
    """What planning needs: {arch, name, context, layers, attention_layers, kv_bytes_per_token (f16 cache),
    draft_kv_bytes_per_token (MTP heads), state_bytes (fixed-size state of one conversation), experts,
    active_experts, nextn, size}. Kept while the file is unchanged: reading a header means skipping the whole
    vocabulary, slow on a phone's shared storage."""
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_size, st.st_mtime)
    if key not in _infos:
        if len(_infos) > 256:
            _infos.clear()
        _infos[key] = _info(path)
    return dict(_infos[key])


def _info(path):
    md = metadata(path)
    arch = md.get("general.architecture", "")

    def g(key, default=None):
        return md.get("%s.%s" % (arch, key), default)

    layers = int(g("block_count", 0) or 0)
    nextn = int(g("nextn_predict_layers", 0) or 0)      # multi-token-prediction heads (MTP drafting)
    main_layers = layers - nextn                          # the heads are extra blocks after the model's own
    heads = g("attention.head_count", 0)
    kv_heads = g("attention.head_count_kv", heads)
    embd = int(g("embedding_length", 0) or 0)
    head_dim = 0
    if isinstance(heads, list):
        heads = max(heads) if heads else 0
    if heads:
        head_dim = embd // int(heads) if embd else 0
    k_len = int(g("attention.key_length", head_dim) or head_dim or 0)
    v_len = int(g("attention.value_length", k_len) or k_len)
    # Which layers keep a KV cache: all of them for a plain transformer; hybrid models (Qwen3.5/3.6, Qwen3-Next,
    # LFM2, Jamba...) only have attention every few layers, the rest keep a small fixed-size state.
    per_layer = kv_heads if isinstance(kv_heads, list) else [kv_heads] * layers
    interval = g("full_attention_interval")
    if interval and not isinstance(kv_heads, list):
        per_layer = [kv_heads if (i + 1) % int(interval) == 0 else 0 for i in range(main_layers)]
    per_layer = [int(x or 0) for x in per_layer][:main_layers]
    attn_layers = sum(1 for x in per_layer if x)
    swa = int(g("attention.sliding_window", 0) or 0)
    kv = sum(x * (k_len + v_len) * 2 for x in per_layer)
    top_kv = max(per_layer) if per_layer else 0
    # The recurrent layers' state (Mamba, Gated DeltaNet): one per conversation, f32, whatever the context length.
    # llama.cpp also keeps copies of it ("context checkpoints") to step back on these models.
    state = 0
    d_state, d_inner = int(g("ssm.state_size", 0) or 0), int(g("ssm.inner_size", 0) or 0)
    if d_state and d_inner:
        conv = int(g("ssm.conv_kernel", 4) or 4)
        groups = int(g("ssm.group_count", 0) or 0)
        state = (main_layers - attn_layers) * (d_state * d_inner + (conv - 1) * (d_inner + 2 * groups * d_state)) * 4
    return {
        "arch": arch, "name": md.get("general.name") or os.path.basename(path),
        "context": int(g("context_length", 0) or 0), "layers": main_layers, "attention_layers": attn_layers,
        "kv_heads": top_kv, "head_dim": k_len,
        "kv_bytes_per_token": kv, "draft_kv_bytes_per_token": nextn * top_kv * (k_len + v_len) * 2,
        "state_bytes": state, "sliding_window": swa,
        "experts": int(g("expert_count", 0) or 0), "active_experts": int(g("expert_used_count", 0) or 0),
        "nextn": nextn, "size": os.path.getsize(path),
    }


def is_gguf(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == MAGIC
    except OSError:
        return False
