/**
 * Amount entry with arithmetic ("120+45.50", "3*60", "(500-120)/2"), like a calculator keypad.
 * A small recursive-descent parser: no eval, only digits, one decimal point per number,
 * + - * / and brackets. The result is rounded to paise and returned as a plain decimal string.
 */
export function evaluateAmount(input: string): string | null {
  const src = input.replace(/[,\s₹]/g, "");
  if (!src) return null;
  if (!/^[0-9.+\-*/()]+$/.test(src)) return null;
  let i = 0;
  const peek = () => src[i];
  function number(): number {
    const start = i;
    while (i < src.length && /[0-9.]/.test(src[i]!)) i++;
    const s = src.slice(start, i);
    if (!s || (s.match(/\./g) ?? []).length > 1) throw new Error("bad number");
    return Number(s);
  }
  function factor(): number {
    if (peek() === "(") {
      i++;
      const v = expr();
      if (peek() !== ")") throw new Error("bracket");
      i++;
      return v;
    }
    if (peek() === "-") {
      i++;
      return -factor();
    }
    return number();
  }
  function term(): number {
    let v = factor();
    while (peek() === "*" || peek() === "/") {
      const op = src[i++];
      const r = factor();
      if (op === "/" && r === 0) throw new Error("divide by zero");
      v = op === "*" ? v * r : v / r;
    }
    return v;
  }
  function expr(): number {
    let v = term();
    while (peek() === "+" || peek() === "-") {
      const op = src[i++];
      const r = term();
      v = op === "+" ? v + r : v - r;
    }
    return v;
  }
  try {
    const v = expr();
    if (i !== src.length || !Number.isFinite(v)) return null;
    const rounded = Math.round(v * 100) / 100;
    if (rounded <= 0) return null;
    return rounded.toFixed(2);
  } catch {
    return null;
  }
}

/** True when the text is more than a plain number, so the UI can show "= 165.50". */
export function isExpression(input: string): boolean {
  return /[+\-*/()]/.test(input.replace(/^\s*-/, ""));
}
