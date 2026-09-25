package com.musab.aragpt2;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Tools executed inside the app itself (device control: open apps, alarms, settings, screen
 * actions ...), next to the tools the Termux agent provides. Entries in {@link #list()} have
 * name / description / parameters and may set "confirm": true for actions that need the user's OK.
 */
public interface LocalTools {
    JSONArray list();

    /** Runs a tool; returns {"ok", "output"}, or null when the name is not a local tool. */
    JSONObject call(String name, JSONObject args) throws Exception;
}
