# Which model (research and measurements)

Target phone: Samsung Galaxy A16. It has an Exynos 1330 or Dimensity 6300 chip (2 fast Cortex-A78 cores and 6 slow Cortex-A55 cores), or a Helio G99 on the 4G model, and 4, 6 or 8 GB of RAM.

The candidates were measured on the same tests with the app's own code on a 4-core CPU:
- **Screen control:** 12 steps of real phone tasks.
- **Chat with tools:** Arabic questions (weather, currency, a logic puzzle, a Levantine-dialect message, facts).

| Model | Type | File | Screen control | Chat & tools | Speed here |
|---|---|---|---|---|---|
| **LFM2.5 2.6B (QAD Q4_0)** | hybrid: conv + attention | 1.6 GB | 5/12 | best: puzzle right, good Arabic, native tools | ~11 tok/s |
| **Qwen3.5 2B** | hybrid: DeltaNet + attention | 1.3 GB | 9/12 | weaker: puzzle wrong | ~16 tok/s |
| Qwen3.5 4B | hybrid | 2.7 GB | 11–12/12 | good | ~7 tok/s (slow on A16) |
| LFM2.5 1.2B | hybrid | 0.7 GB | 2/12 | ok for 4 GB phones | ~25 tok/s |
| LFM2.5 8B-A1B | MoE (1.5B active) | 4.8 GB | 2/12 | odd JSON-style answers | ~14 tok/s |

**Recommendation for the A16:** the "⭐ الحزمة المثالية" pack in the model list.
- LFM2.5 2.6B is the chat model.
- Qwen3.5 2B is assigned to screen control automatically.

Both stay loaded together on a 6 GB phone. On a 4 GB phone the pack picks LFM2.5 1.2B for chat, and Qwen3.5 2B is loaded only for screen tasks (the model pool frees the other one when RAM is short).

Links (free):
- https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF/resolve/main/LFM2.5-2.6B-QAD-Q4_0.gguf
- https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf
- https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF/resolve/main/LFM2.5-1.2B-Instruct-QAD-Q4_0.gguf

Downloads made from the app go to the phone's **Download/NewAl** folder.

## Tried and not used

**Speculative decoding for the hybrid models.** Rollback works in this llama.cpp version, but on a CPU a Qwen3.5 0.8B draft made Qwen3.5 2B and 4B slower (0.6–0.8x), and the output was not byte-identical. LFM2.5 2.6B and LFM2.5 350M use different vocabularies. LiquidAI's DSpark drafters need a special drafter architecture. Speculative decoding stays on only for Qwen2.5 3B+ with a 0.5B draft, where it measured +22%.

**LFM2.5 using too many tools.** It is agent-trained and kept calling tools; one answer took 190 s. The app now allows at most one tool round when it has already fetched the facts (two otherwise) and skips repeated calls. That brought it to about 30–45 s with correct Arabic answers.

## Speed on the phone

The first time a model loads, the app measures generation speed with 2, 3, 4, 6 and all cores and keeps the fastest (☰ → ⚡ to measure again). Prompt reading always uses every core.

## 🖥 The computer's brain

When NewAl runs on the user's computer, the phone can use its brain (Qwen3.6-35B-A3B) instead of a model on the phone. It is much stronger than any model that fits a phone, and the phone does not heat up or drain its battery.

To connect:
1. On the computer, open NewAl and turn on «📱 الهاتف».
2. On the phone, open the model menu at the top and choose «🖥 عقل الكمبيوتر».
3. Tap «📷 امسح QR» and scan the code on the computer. You can also paste the link that «📋 نسخ الرابط» copies on the computer.

The phone and the computer must reach each other. That works over the same Wi-Fi, over the phone's hotspot with the computer joined to it, or over a USB cable with USB tethering.

How it works:
- The app checks NewAl first: the key, the llama.cpp engine, and the Qwen3.6 model. It then has the computer load the brain right away, because the first load takes a minute or more.
- Every role uses the computer's brain: chat, screen control, and coding with Termux. The phone's models are unloaded to free RAM.
- The key travels as a bearer token over the local network, like the QR link itself.
- At start-up the app reconnects to the computer. When the computer can't be reached, the app uses the phone's model and says why.
- Choosing a model on the phone switches back to it. «افصل» in the same dialog does too.

Tested:
- `RemoteBrainTest` runs against a fake NewAl.
- The same client ran live against the real desktop server and llama.cpp, with Qwen3.5 0.8B standing in for Qwen3.6. That covered Arabic streaming, thinking, the manager's grammar, a tool round, stopping mid-answer (the computer stops at once), and a wrong key.
