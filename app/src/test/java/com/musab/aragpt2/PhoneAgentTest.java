package com.musab.aragpt2;

import static org.junit.Assert.*;

import java.time.LocalTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import org.junit.Test;

public class PhoneAgentTest {
    private static final LocalTime NOON = LocalTime.of(12, 5);

    private static String one(String text) {
        List<QuickCommands.Command> c = QuickCommands.parse(text, NOON);
        assertNotNull("not understood: " + text, c);
        assertEquals(text, 1, c.size());
        return c.get(0).toString();
    }

    @Test public void opensAppsInArabicAndEnglish() {
        assertEquals("open_app{name=يوتيوب}", one("افتح يوتيوب"));
        assertEquals("open_app{name=واتساب}", one("افتحلي تطبيق الواتساب".replace("الواتساب", "واتساب")));
        assertEquals("open_app{name=instagram}", one("Open Instagram"));
        assertEquals("camera{}", one("افتح الكاميرا"));
        assertEquals("settings{page=wifi}", one("افتح اعدادات الواي فاي"));
        assertEquals("settings{page=main}", one("افتح الإعدادات"));
    }

    @Test public void deviceSwitchesAndButtons() {
        assertEquals("flashlight{on=true}", one("شغل الكشاف"));
        assertEquals("flashlight{on=false}", one("طفي الضو"));
        assertEquals("flashlight{on=false}", one("flashlight off"));
        assertEquals("toggle{setting=bluetooth, on=true}", one("شغّل البلوتوث"));
        assertEquals("toggle{setting=wifi, on=false}", one("سكر الواي فاي"));
        assertEquals("volume{level=up}", one("علي الصوت"));
        assertEquals("volume{level=down}", one("وطي الصوت"));
        assertEquals("volume{level=40}", one("الصوت على 40"));
        assertEquals("volume{level=mute}", one("حط الجوال على الصامت"));
        assertEquals("media{action=pause}", one("وقف الأغنية"));
        assertEquals("media{action=next}", one("الأغنية الجاية"));
        assertEquals("press{button=back}", one("ارجع"));
        assertEquals("press{button=home}", one("الشاشة الرئيسية"));
        assertEquals("press{button=screenshot}", one("خذ سكرين شوت"));
        assertEquals("status{}", one("قديش البطارية؟"));
    }

    @Test public void alarmsAndTimers() {
        assertEquals("alarm{hour=7, minute=0, label=}", one("صحيني الساعة ٧ الصبح"));
        assertEquals("alarm{hour=18, minute=30, label=}", one("حط منبه 6 ونص المسا"));
        assertEquals("alarm{hour=6, minute=45, label=}", one("منبه 7 الا ربع الصبح"));
        assertEquals("alarm{hour=19, minute=15, label=}", one("set an alarm for 7:15 pm"));
        // No am/pm at 12:05: 7 means 19:00 today, not 07:00 tomorrow.
        assertEquals("alarm{hour=19, minute=0, label=}", one("نبهني الساعة 7"));
        assertEquals("timer{seconds=300, label=}", one("مؤقت 5 دقايق"));
        assertEquals("timer{seconds=600, label=الاكل}", one("ذكرني بعد 10 دقائق عشان الاكل"));
        assertEquals("timer{seconds=5400, label=}", one("تايمر ساعة ونص"));
        assertEquals("timer{seconds=120, label=}", one("set a timer for 2 minutes".replace("set a ", "")));
    }

    @Test public void callsAndMessages() {
        assertEquals("call{who=باحمد, app=phone}", one("اتصل بأحمد"));
        assertEquals("call{who=0991234567, app=phone}", one("دق على ٠٩٩١٢٣٤٥٦٧"));
        assertEquals("call{who=mom, app=phone}", one("call mom"));
        assertEquals("call{who=سارة, app=whatsapp}".replace("ة", "ه"), one("اتصل على سارة واتساب"));
        assertEquals("message{who=لاحمد علي, text=رح اتاخر شوي, app=whatsapp}", one("ابعت لأحمد علي على الواتس: رح اتأخر شوي"));
        assertEquals("message{who=لماما, text=رح اتاخر, app=whatsapp}", one("ابعت لماما عالواتس انو رح اتأخر"));
        assertEquals("message{rest=لسامر مرحبا كيفك, app=sms}", one("ارسل رسالة نصية لسامر مرحبا كيفك"));
        assertEquals("message{who=john, text=I'm on my way, app=whatsapp}".toLowerCase(), one("send a whatsapp message to John: I'm on my way").toLowerCase());
    }

    @Test public void searchesAndMaps() {
        assertEquals("youtube{query=اغاني فيروز}", one("ابحث في يوتيوب عن اغاني فيروز"));
        assertEquals("youtube{query=اغاني فيروز}", one("افتح يوتيوب وابحث عن اغاني فيروز"));
        assertEquals("web_search{query=سعر الذهب اليوم}", one("ابحث عن سعر الذهب اليوم"));
        assertEquals("maps{destination=المطار}", one("خذني على المطار"));
        assertEquals("maps{destination=اقرب صيدليه}", one("وين أقرب صيدلية"));
    }

    @Test public void chainsCommandsButKeepsMessageTextWhole() {
        List<QuickCommands.Command> c = QuickCommands.parse("شغل الكشاف وبعدين علي الصوت", NOON);
        assertEquals("[flashlight{on=true}, volume{level=up}]", String.valueOf(c));
        c = QuickCommands.parse("افتح الكاميرا ثم شغل البلوتوث", NOON);
        assertEquals("[camera{}, toggle{setting=bluetooth, on=true}]", String.valueOf(c));
        // "و" inside the message is part of the message.
        c = QuickCommands.parse("ابعت لأحمد: جيب خبز وافتح الباب", NOON);
        assertEquals(1, c.size());
        assertEquals("جيب خبز وافتح الباب", c.get(0).arg("text"));
    }

    @Test public void unknownRequestsGoToTheModel() {
        assertNull(QuickCommands.parse("احجزلي طاولة بمطعم الشام يوم الخميس", NOON));
        assertNull(QuickCommands.parse("شو الطقس بكرة وارسل النتيجة لأمي", NOON));
        assertNull(QuickCommands.parse("لخص آخر رسالة وصلتني", NOON));
    }

    @Test public void telegramAndOtherAppTasksGoToTheScreenAgent() {
        assertNull(QuickCommands.parse("ابعت لسامر على تلغرام: وصلت", NOON));
        assertTrue(QuickCommands.looksLikePhoneTask("ابعت لسامر على تلغرام: وصلت"));
        assertTrue(QuickCommands.looksLikePhoneTask("احجزلي طاولة بمطعم الشام"));
        assertTrue(QuickCommands.looksLikePhoneTask("لايك لآخر فيديو على يوتيوب"));
        assertTrue(QuickCommands.looksLikePhoneTask("delete the last photo"));
        assertFalse(QuickCommands.looksLikePhoneTask("شو عاصمة فرنسا؟"));
        assertFalse(QuickCommands.looksLikePhoneTask("اكتب لي قصيدة عن البحر"));
        assertFalse(QuickCommands.looksLikePhoneTask("explain recursion"));
    }

    @Test public void appNamesInArabic() {
        assertTrue(AppMatcher.score("يوتيوب", "YouTube", "com.google.android.youtube") >= 90);
        assertTrue(AppMatcher.score("الواتس", "WhatsApp", "com.whatsapp") >= 90);
        assertTrue(AppMatcher.score("انستا", "Instagram", "com.instagram.android") >= 70);
        assertTrue(AppMatcher.score("الحاسبة", "Calculator", "com.google.android.calculator") >= 90);
        assertEquals(100, AppMatcher.score("يوتيوب", "يوتيوب", "com.google.android.youtube"));
        assertTrue(AppMatcher.score("الكاميرا", "Camera", "com.android.camera") > AppMatcher.score("الكاميرا", "Camera Translator", "x.y"));
        assertEquals(0, AppMatcher.score("الباب", "YouTube", "com.google.android.youtube"));
    }

    @Test public void contactMatching() {
        List<ContactMatcher.Contact> all = Arrays.asList(
                new ContactMatcher.Contact("أحمد علي", "+963 991 234 567"),
                new ContactMatcher.Contact("ماما", "0933 111 222"),
                new ContactMatcher.Contact("سامر الحلبي", "0944 555 666"),
                new ContactMatcher.Contact("Basel", "0955 000 111"),
                new ContactMatcher.Contact("أحمد خالد", "0966 000 222"));
        assertEquals("أحمد علي", ContactMatcher.find("لاحمد علي", all).get(0).name);
        assertEquals("ماما", ContactMatcher.find("لماما", all).get(0).name);
        assertEquals(2, ContactMatcher.find("باحمد", all).size());          // two Ahmads: the app asks which one
        assertEquals("Basel", ContactMatcher.find("basel", all).get(0).name);
        assertTrue(ContactMatcher.find("خالد الشامي", all).isEmpty());

        ContactMatcher.Split s = ContactMatcher.split("لسامر مرحبا كيفك", all);
        assertEquals("سامر الحلبي", s.contacts.get(0).name);
        assertEquals("مرحبا كيفك", s.text);
        s = ContactMatcher.split("احمد علي وينك", all);
        assertEquals(1, s.contacts.size());
        assertEquals("وينك", s.text);

        assertEquals("963991234567", ContactMatcher.international("+963 991 234 567", "sy"));
        assertEquals("963933111222", ContactMatcher.international("0933 111 222", "sy"));
        assertEquals("4915112345678", ContactMatcher.international("0151 12345678", "DE"));
        assertEquals("963933111222", ContactMatcher.international("00963933111222", "de"));
        assertTrue(ContactMatcher.isNumber("٠٩٩١٢٣٤٥٦٧"));
        assertFalse(ContactMatcher.isNumber("أحمد"));
    }

    @Test public void actionParsingAndGrammarShape() {
        assertEquals("tap(4)", PhoneAction.parse("tap(4)").toString());
        assertEquals("type(7, \"رح اتأخر \\\"شوي\\\"\")", PhoneAction.parse("type(7, \"رح اتأخر \\\"شوي\\\"\")").toString());
        assertEquals("رح اتأخر \"شوي\"", PhoneAction.parse("type(7, \"رح اتأخر \\\"شوي\\\"\")").text);
        assertEquals(PhoneAction.Kind.SCROLL, PhoneAction.parse("Reason: need more\nscroll(down)").kind);
        assertEquals("need more", PhoneAction.reason("Reason: need more\nscroll(down)"));
        assertEquals("done(\"تم\")", PhoneAction.parse("done(\"تم\")").toString());
        assertNull(PhoneAction.parse("tap()"));
        assertNull(PhoneAction.parse("I will tap the button"));
        assertTrue(PhoneAction.GRAMMAR.startsWith("root ::= "));
    }

    @Test public void screenRenderingMarksEmptyFieldsAndSwitches() {
        ScreenState s = new ScreenState("com.whatsapp", "WhatsApp", Arrays.asList(
                new ScreenState.Element(1, "field", "Message", false, null, "entry", true),
                new ScreenState.Element(2, "field", "hi", true, null, "entry", false),
                new ScreenState.Element(3, "switch", "Bluetooth", false, true, ""),
                new ScreenState.Element(4, "button", "", false, null, "send")), true);
        String r = s.render();
        assertTrue(r, r.startsWith("App: WhatsApp (com.whatsapp)\n"));
        assertTrue(r, r.contains("[1] field \"Message\" (empty)"));
        assertTrue(r, r.contains("[2] field containing \"hi\" focused"));
        assertTrue(r, r.contains("[3] switch \"Bluetooth\" on"));
        assertTrue(r, r.contains("[4] button (send)"));
        assertTrue(r, r.contains("scroll"));
    }

    // ------------------------------------------------------------------ the loop, on a simulated phone

    /** A tiny WhatsApp: chat list → chat → typed → sent. */
    private static final class FakePhone implements PhoneAgent.Device {
        int screen = 0;
        String typed = "";
        final List<String> log = new ArrayList<>();

        @Override public ScreenState observe() {
            List<ScreenState.Element> e = new ArrayList<>();
            switch (screen) {
                case 0: e.add(new ScreenState.Element(1, "item", "WhatsApp", false, null, "")); return new ScreenState("launcher", "Launcher", e, false);
                case 1: e.add(new ScreenState.Element(1, "item", "Ahmad, see you", false, null, "")); return new ScreenState("com.whatsapp", "WhatsApp", e, false);
                case 2:
                    e.add(new ScreenState.Element(1, "field", typed.isEmpty() ? "Message" : typed, false, null, "entry", typed.isEmpty()));
                    e.add(new ScreenState.Element(2, "button", typed.isEmpty() ? "Voice message" : "Send", false, null, typed.isEmpty() ? "voice" : "send"));
                    return new ScreenState("com.whatsapp", "WhatsApp", e, false);
                default:
                    e.add(new ScreenState.Element(1, "text", typed + " ✓", false, null, ""));
                    return new ScreenState("com.whatsapp", "WhatsApp", e, false);
            }
        }

        @Override public String perform(PhoneAction a, ScreenState on) {
            log.add(a.toString());
            if (a.kind == PhoneAction.Kind.OPEN_APP) { screen = 1; return "opened WhatsApp"; }
            if (a.kind == PhoneAction.Kind.TAP && screen == 1) { screen = 2; return "ok"; }
            if (a.kind == PhoneAction.Kind.TYPE && screen == 2) { typed = a.text; return "ok"; }
            if (a.kind == PhoneAction.Kind.TAP && screen == 2 && !typed.isEmpty() && a.target == 2) { screen = 3; return "ok"; }
            return "ok";
        }
    }

    private static PhoneAgent.Model scripted(String... replies) {
        int[] i = {0};
        return (turns, grammar) -> {
            assertEquals(PhoneAction.GRAMMAR, grammar);
            return replies[Math.min(i[0]++, replies.length - 1)];
        };
    }

    private static final class Recorder implements PhoneAgent.Listener {
        final List<String> steps = new ArrayList<>();
        boolean allow = true;
        int confirms;
        @Override public void onStep(int step, PhoneAction action, String reason, String result) { steps.add(action + " → " + result); }
        @Override public boolean confirm(String what) { confirms++; return allow; }
    }

    @Test public void sendsAMessageStepByStepAndConfirmsTheSend() throws Exception {
        FakePhone phone = new FakePhone();
        Recorder r = new Recorder();
        PhoneAgent agent = new PhoneAgent(phone, scripted("open_app(\"WhatsApp\")", "tap(1)", "type(1, \"hi\")", "tap(2)", "done(\"Sent\")"), r);
        PhoneAgent.Result res = agent.run("send hi to Ahmad on WhatsApp");
        assertTrue(res.done);
        assertEquals("Sent", res.message);
        assertEquals(Arrays.asList("open_app(\"WhatsApp\")", "tap(1)", "type(1, \"hi\")", "tap(2)"), phone.log);
        assertEquals("tapping Send needs the user's OK", 1, r.confirms);
        assertTrue(r.steps.get(1), r.steps.get(1).endsWith("(screen changed)"));
    }

    @Test public void declinedSendStopsTheTask() throws Exception {
        FakePhone phone = new FakePhone();
        Recorder r = new Recorder();
        r.allow = false;
        PhoneAgent.Result res = new PhoneAgent(phone, scripted("open_app(\"WhatsApp\")", "tap(1)", "type(1, \"hi\")", "tap(2)", "done(\"Sent\")"), r)
                .run("send hi to Ahmad");
        assertFalse(res.done);
        assertEquals(3, phone.log.size());          // the send tap never happened
    }

    @Test public void stopsWhenTheModelRepeatsAUselessStep() throws Exception {
        FakePhone phone = new FakePhone();
        phone.screen = 3;
        Recorder r = new Recorder();
        PhoneAgent.Result res = new PhoneAgent(phone, scripted("scroll(down)"), r).run("find the settings");
        assertFalse(res.done);
        assertTrue(res.message, res.steps <= 4);
    }

    @Test public void promptShowsGoalHistoryAndScreen() {
        FakePhone phone = new FakePhone();
        String p = PhoneAgent.prompt("send hi", Arrays.asList("1. open_app(\"WhatsApp\") → ok (screen changed)"), phone.observe());
        assertTrue(p, p.startsWith("Goal: send hi\nSteps so far:\n1. open_app"));
        assertTrue(p, p.contains("Current screen:\nApp: Launcher (launcher)\n[1] item \"WhatsApp\""));
        assertTrue(p, p.endsWith("Next action:"));
    }
}
