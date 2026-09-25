package com.musab.aragpt2;

/** Receives generated text as the model produces it. */
public interface TextListener { void onText(String text) throws Exception; }
