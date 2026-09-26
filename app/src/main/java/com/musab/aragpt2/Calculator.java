package com.musab.aragpt2;

import java.math.BigDecimal;
import java.math.MathContext;
import java.math.RoundingMode;

/**
 * Exact arithmetic for the assistant (small models get multiplication and percentages wrong).
 * Supports + - * / ^ %, parentheses, unary minus, Arabic digits and ×÷, and sqrt, sin, cos, tan,
 * log (base 10), ln, abs, round, pi, e.
 */
final class Calculator {
    private final String s;
    private int i;

    private Calculator(String s) { this.s = s; }

    /** The value of {@code expression}; throws IllegalArgumentException when it is not valid. */
    static BigDecimal eval(String expression) {
        String t = ArabicText.norm(expression).replace('×', '*').replace('÷', '/').replace('٫', '.')
                .replace("**", "^").replaceAll("(?<=\\d),(?=\\d{3}\\b)", "").replace(',', '.');
        Calculator c = new Calculator(t);
        BigDecimal v = c.sum();
        c.skip();
        if (c.i != t.length()) throw new IllegalArgumentException("unexpected '" + t.charAt(c.i) + "'");
        return v;
    }

    /** A readable result: up to 12 significant digits, no trailing zeros, no exponent for normal sizes. */
    static String format(BigDecimal v) {
        BigDecimal r = v.round(new MathContext(12, RoundingMode.HALF_EVEN)).stripTrailingZeros();
        return r.abs().compareTo(new BigDecimal("1e15")) < 0 && (r.scale() <= 12) ? r.toPlainString() : r.toString();
    }

    /** True when the whole text is a calculation ("15% of 240", "(3+4)*12", "٢٥×٤"). */
    static boolean looksLikeMath(String text) {
        String t = ArabicText.norm(text).replaceAll("[=?؟ ]+$", "").trim();
        if (!t.matches("[0-9.,+\\-*/^%()×÷ \\sa-z]+") || !t.matches(".*\\d.*") || !t.matches(".*[+\\-*/^%×÷].*")) return false;
        try { eval(percentOf(t)); return true; } catch (RuntimeException e) { return false; }
    }

    /** "15% of 240" / "15% من 240" → "15/100*240". */
    static String percentOf(String t) {
        return ArabicText.norm(t).replaceAll("([0-9.]+)\\s*%\\s*(?:of|من)\\s*([0-9.]+)", "($1/100*$2)");
    }

    private static final java.util.regex.Pattern EXPR = java.util.regex.Pattern.compile(
            "(?:[0-9.]+\\s*%\\s*(?:of|من)\\s*[0-9.]+)|(?:\\(?\\s*-?[0-9][0-9.,]*\\s*\\)?(?:\\s*[-+*/^×÷]\\s*\\(?\\s*-?[0-9][0-9.,]*\\s*\\)?)+)");

    /**
     * Calculations written inside a sentence ("كم 17.5% من 2340", "what is 12*37+5") with their
     * exact results, e.g. "17.5% من 2340 = 409.5"; empty when there are none.
     */
    static java.util.List<String> findAndSolve(String text) {
        java.util.List<String> out = new java.util.ArrayList<>();
        java.util.regex.Matcher m = EXPR.matcher(ArabicText.norm(text));
        while (m.find() && out.size() < 4) {
            String e = m.group().trim();
            try { out.add(e + " = " + format(eval(percentOf(e)))); } catch (RuntimeException ignored) {}
        }
        return out;
    }

    private static final MathContext MC = MathContext.DECIMAL64;

    private void skip() { while (i < s.length() && s.charAt(i) == ' ') i++; }

    private boolean eat(char c) {
        skip();
        if (i < s.length() && s.charAt(i) == c) { i++; return true; }
        return false;
    }

    private BigDecimal sum() {
        BigDecimal v = product();
        while (true) {
            if (eat('+')) v = v.add(product(), MC);
            else if (eat('-')) v = v.subtract(product(), MC);
            else return v;
        }
    }

    private BigDecimal product() {
        BigDecimal v = power();
        while (true) {
            if (eat('*')) v = v.multiply(power(), MC);
            else if (eat('/')) {
                BigDecimal d = power();
                if (d.signum() == 0) throw new IllegalArgumentException("division by zero");
                v = v.divide(d, MC);
            } else return v;
        }
    }

    private BigDecimal power() {
        BigDecimal base = unary();
        if (eat('^')) {
            BigDecimal exp = power();   // right-associative
            if (exp.stripTrailingZeros().scale() <= 0 && exp.abs().compareTo(BigDecimal.valueOf(999)) <= 0) {
                int n = exp.intValueExact();
                return n >= 0 ? base.pow(n, MC) : BigDecimal.ONE.divide(base.pow(-n, MC), MC);
            }
            return BigDecimal.valueOf(Math.pow(base.doubleValue(), exp.doubleValue()));
        }
        return base;
    }

    private BigDecimal unary() {
        if (eat('-')) return unary().negate();
        if (eat('+')) return unary();
        BigDecimal v = atom();
        if (eat('%')) v = v.divide(BigDecimal.valueOf(100), MC);
        return v;
    }

    private BigDecimal atom() {
        skip();
        if (eat('(')) {
            BigDecimal v = sum();
            if (!eat(')')) throw new IllegalArgumentException("missing )");
            return v;
        }
        int start = i;
        while (i < s.length() && (Character.isDigit(s.charAt(i)) || s.charAt(i) == '.')) i++;
        if (i > start) return new BigDecimal(s.substring(start, i));
        while (i < s.length() && Character.isLetter(s.charAt(i))) i++;
        String name = s.substring(start, i);
        switch (name) {
            case "pi": return BigDecimal.valueOf(Math.PI);
            case "e": return BigDecimal.valueOf(Math.E);
            case "": throw new IllegalArgumentException(i < s.length() ? "unexpected '" + s.charAt(i) + "'" : "expression ended early");
            default: break;
        }
        if (!eat('(')) throw new IllegalArgumentException("unknown name " + name);
        BigDecimal a = sum();
        if (!eat(')')) throw new IllegalArgumentException("missing )");
        double x = a.doubleValue();
        switch (name) {
            case "sqrt": if (x < 0) throw new IllegalArgumentException("sqrt of a negative number"); return BigDecimal.valueOf(Math.sqrt(x));   // BigDecimal.sqrt needs Android 13
            case "abs": return a.abs();
            case "round": return a.setScale(0, RoundingMode.HALF_UP);
            case "sin": return BigDecimal.valueOf(Math.sin(Math.toRadians(x)));
            case "cos": return BigDecimal.valueOf(Math.cos(Math.toRadians(x)));
            case "tan": return BigDecimal.valueOf(Math.tan(Math.toRadians(x)));
            case "log": return BigDecimal.valueOf(Math.log10(x));
            case "ln": return BigDecimal.valueOf(Math.log(x));
            default: throw new IllegalArgumentException("unknown function " + name);
        }
    }
}
