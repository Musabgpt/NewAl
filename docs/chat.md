# Chat (ChatGPT-style)

The main screen is a normal chat, like ChatGPT, running fully on the phone. Markdown answers render with headings, lists, tables, and code blocks with copy and highlighting.

- **Conversations:** kept in the ☰ side panel. Long-press a conversation to rename or delete it.
- **Actions under each answer:** ⧉ copy, ↻ regenerate, 🔈 read aloud, ↗ share.
- **Editing:** long-press your own message to edit it and send it again.
- **Attach:** ＋ attaches a text or code file to the message.
- **Voice:** 🎙 for voice input.

## Tools

All tools are free and need no account or API key.

| Tool | Source | When it runs |
|---|---|---|
| Calculator | exact arithmetic in the app | Any calculation in the question is solved by code, and the result is given to the model. The model can also call it. |
| Weather | Open-Meteo | Questions about weather (a city is detected in Arabic or English), or when the model calls it |
| Web search | Bing RSS, then DuckDuckGo, then Wikipedia. The best page is read and the relevant paragraphs are kept. | 🌐 is on, the question is time-sensitive (اليوم، سعر، أخبار، latest…), or the model calls it |
| Read page, Wikipedia, currency (open.er-api.com), time | | When the model calls them |

Tools are offered in Qwen's own function-calling format: JSON calls for Qwen2.5/Qwen3 and XML calls for Qwen3.5. The format is chosen from the model's template. Answers cite web results as [n], and the sources are listed under the answer.

The deterministic steps (calculator, weather, search) do not depend on the model deciding to use a tool. That is what makes small models accurate here.

**Measured with Qwen3.5-4B** on three questions (percentage, Damascus weather, today's gold price): all three answered correctly with real data.

## 💭 Thinking

With 💭 on, Qwen3 / Qwen3.5 models reason in a `<think>` block first, which is slower but more accurate on hard problems. The reasoning is folded above the answer. With it off, the bridge adds the models' own "no thinking" switch, so answers start immediately.

## Everyday phone commands in the chat

Commands like «افتح يوتيوب», «اتصل بأحمد», «ابعت لماما: …» and «صحيني 7» run at once, even in the chat. Longer tasks on the screen need the 📱 mode (see phone-agent.md).
