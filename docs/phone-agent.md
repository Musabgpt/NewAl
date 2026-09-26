# Phone agent

The 📱 mode turns the app into an assistant that controls the phone. It has two layers.

## 1. Instant commands (no model)

`QuickCommands` understands everyday requests in Arabic (MSA and Levantine) and English, and runs
them in milliseconds. Because no model is involved, they cannot be misread.

| Request | What happens |
|---|---|
| افتح يوتيوب / open instagram | Opens the best-matching installed app. Arabic names and aliases work: الواتس، انستا، الحاسبة… |
| اتصل بأحمد / دق على 0991234567 | Finds the contact (asks which one if two match) and calls. Without the permission it opens the dialer. |
| ابعت لماما عالواتس: رح اتأخر | Opens the WhatsApp chat through a wa.me link with the text, then taps Send. |
| ارسل رسالة نصية لسامر مرحبا | Sends an SMS (with permission), or opens the composer. |
| صحيني الساعة 7 الصبح / مؤقت 5 دقايق | Sets an alarm or a timer. Without am/pm, the next time that hour comes round is used. |
| شغّل الكشاف / علّي الصوت / الأغنية الجاية | Flashlight, volume, media keys. |
| شغّل البلوتوث / سكّر الواي فاي | Opens the settings page and flips the switch on screen. Android does not let apps toggle these directly. |
| ابحث في يوتيوب عن… / خذني على المطار | YouTube search, web search, maps. |
| ارجع / الشاشة الرئيسية / سكرين شوت | System buttons. |

Commands can be chained: «شغّل الكشاف وبعدين علّي الصوت». In a message, a «و» belongs to the message text.

Sending a message and calling ask first («✅ يسأل قبل الإرسال»). This can be switched off.

## 2. Screen agent (model)

Anything else goes to `PhoneAgent`, for example «احجزلي طاولة», «ابعت لسامر على تلغرام», or «افتح الإعدادات وشوف نسخة أندرويد». Each step works like this:

1. `ScreenControlService` reads the screen from the accessibility tree as a short numbered list:
   ```
   App: WhatsApp (com.whatsapp)
   [7] field "Message" (empty)
   [9] button "Send"
   ```
2. The model answers with exactly one action. A GBNF grammar limits it to `open_app`, `tap`, `type`, `enter`, `scroll`, `back`, `home`, `wait`, `done` and `ask`.
3. The app performs the action, waits until the screen settles, and checks whether anything changed.

Safety checks:
- Repeating the same action on an unchanged screen stops the task.
- Four actions without effect stop the task.
- Taps on send, pay, call or delete buttons need confirmation. The question appears in a bar over the other app.
- A ⏹ button on the top bar stops the task at any time.

## Measured model choice

12 real steps of screen tasks: WhatsApp send, YouTube search, Bluetooth, and a question answered from the screen. Instructions were in Arabic and English, measured on a 4-core CPU with greedy decoding and the grammar.

| Model (Q4_K_M) | Size | Correct steps | Time per step |
|---|---|---|---|
| **Qwen3.5-4B** | 2.7 GB | **12/12**, then 11/12 after prompt changes | 5–7 s (with the state checkpoint) |
| Qwen3.5-2B | 1.3 GB | 9/12 | ~2.5 s |
| Qwen3-4B-Instruct-2507 | 2.5 GB | 8–9/12 | ~5 s |
| Qwen3-1.7B | 1.1 GB | 5/12 | ~2 s |
| Qwen3.5-0.8B | 0.5 GB | 3–5/12 (declares done too early) | ~2 s |
| LFM2-1.2B-Tool | 0.7 GB | 2/12 | ~3 s |

Recommendation:
- Qwen3.5-4B for phones with 8 GB of RAM or more.
- Qwen3.5-2B for phones with 6 GB.
- Smaller models are not reliable enough to operate the screen.

Qwen3.5 is a hybrid (partly recurrent) model, so the usual KV-prefix reuse does not work. Instead, the bridge saves the model state at the point where consecutive prompts diverge (system prompt, goal, earlier steps) and restores it for the next step, as llama-server does. That cut the 4B step time from about 16 s to 5–7 s. The bridge also adds Qwen's empty `<think></think>` block so the model answers directly.

## Setup (once)

1. Enable **NewAl screen control** in Accessibility settings (🖐 chip). On Android 13+, if the setting is marked restricted: App info → ⋮ → Allow restricted settings.
2. Grant permissions when first asked: contacts, calls and SMS are each requested when first needed.
3. Optional: tap **🏠 اجعله مساعد الهاتف** and choose NewAl as the digital assistant app. Long-pressing Home then opens it listening.

Voice input uses the phone's speech recogniser, which works offline if the Arabic language pack is installed. Replies are spoken with the phone's text-to-speech (🔈 chip).
