package com.musab.aragpt2;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class AgentsAndSkillsTest {
    @Test public void slashCommandsPickAgentAndSkill() {
        Skills.Parsed p = Skills.parse("/test /api write tests for my server");
        assertEquals(AgentProfile.TESTER, p.agent);
        assertEquals("api", p.skill.id);
        assertEquals("write tests for my server", p.text);

        Skills.Parsed plain = Skills.parse("/notacommand hello");
        assertNull(plain.agent);
        assertNull(plain.skill);
        assertEquals("/notacommand hello", plain.text);
    }

    @Test public void autoAgentFollowsTheRequest() {
        assertEquals(AgentProfile.CODER, AgentProfile.choose("write a calculator", false));
        assertEquals(AgentProfile.AUTOMATOR, AgentProfile.choose("كم نسبة البطارية", false));
        assertEquals(AgentProfile.EXPLAINER, AgentProfile.choose("how does main.py work?", true));
        assertEquals(AgentProfile.CODER, AgentProfile.choose("how does main.py work?", false));
        assertEquals(AgentProfile.TESTER, AgentProfile.choose("add unit tests", true));
        String longTask = "build a program that " + "does many things and ".repeat(10);
        assertEquals(AgentProfile.ARCHITECT, AgentProfile.choose(longTask, false));
        assertEquals(AgentProfile.FIXER, AgentProfile.choose(
                "fix this\nTraceback (most recent call last):\n  File \"a.py\", line 1\nNameError: x", true));
    }

    @Test public void skillsMatchByKeywordsOnlyWhenClear() {
        assertEquals("interactive", Skills.match("write me a calculator game").id);
        assertEquals("scraper", Skills.match("scrape a website with requests").id);
        assertNull(Skills.match("hello"));
    }

    @Test public void userSkillFromMarkdown() {
        Skills.Skill s = Skills.fromMarkdown("pdf", "# PDF tools | pdf, merge pdf\nUse pypdf. Never overwrite inputs.");
        assertEquals("PDF tools", s.title);
        Skills.setUserSkills(java.util.List.of(s));
        assertEquals("pdf", Skills.match("merge pdf files please").id);
        assertTrue(Skills.byId("pdf").instructions.contains("pypdf"));
        Skills.setUserSkills(java.util.List.of());
    }
}
