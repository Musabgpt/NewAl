package com.musab.aragpt2;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.junit.Test;

public class OrchestratorTest {
    @Test public void parsesRoutesAndPlans() {
        Orchestrator.Route r = Orchestrator.parse("ROUTE: code\nPLAN: app.py - server\nPLAN: tests/test_app.py\nPLAN: run tests");
        assertEquals(Orchestrator.Kind.CODE, r.kind);
        assertEquals(3, r.plan.size());
        assertEquals(AgentProfile.ARCHITECT, r.agent());
        assertEquals("- app.py - server\n- tests/test_app.py\n- run tests\n", r.planText());

        assertEquals(AgentProfile.CODER, Orchestrator.parse("ROUTE: code\nPLAN: main.py").agent());
        assertEquals(AgentProfile.AUTOMATOR, Orchestrator.parse("ROUTE: phone").agent());
        assertEquals(AgentProfile.EXPLAINER, Orchestrator.parse("ROUTE: explain").agent());
        assertNull(Orchestrator.parse("ROUTE: chat").agent());
        assertNull(Orchestrator.parse("garbage").agent());
    }
}
