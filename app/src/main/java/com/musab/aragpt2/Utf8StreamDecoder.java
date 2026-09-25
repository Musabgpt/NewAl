package com.musab.aragpt2;

import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CharsetDecoder;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;

/**
 * Decodes a UTF-8 byte stream that arrives in arbitrary pieces. Bytes of a character split
 * across pieces are kept until the character is complete.
 */
final class Utf8StreamDecoder {
    private final CharsetDecoder decoder = StandardCharsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPLACE)
            .onUnmappableCharacter(CodingErrorAction.REPLACE);
    private ByteBuffer pending = ByteBuffer.allocate(0);

    String decode(byte[] data, int off, int len) {
        ByteBuffer in;
        if (pending.hasRemaining()) {
            in = ByteBuffer.allocate(pending.remaining() + len);
            in.put(pending).put(data, off, len);
            in.flip();
        } else {
            in = ByteBuffer.wrap(data, off, len);
        }
        CharBuffer out = CharBuffer.allocate(in.remaining() + 1);
        decoder.decode(in, out, false);
        pending = in.hasRemaining() ? ByteBuffer.wrap(copyRemaining(in)) : ByteBuffer.allocate(0);
        out.flip();
        return out.toString();
    }

    /** End of stream: an unfinished trailing character becomes U+FFFD rather than vanishing. */
    String flush() {
        CharBuffer out = CharBuffer.allocate(pending.remaining() + 2);
        decoder.decode(pending, out, true);
        decoder.flush(out);
        pending = ByteBuffer.allocate(0);
        decoder.reset();
        out.flip();
        return out.toString();
    }

    private static byte[] copyRemaining(ByteBuffer b) {
        byte[] r = new byte[b.remaining()];
        b.get(r);
        return r;
    }
}
