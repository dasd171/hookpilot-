/**
 * Number formatting that reproduces the firmware's C printf and the Python port's f-strings, so the
 * rule-trace text is byte-identical across implementations.
 */

/**
 * printf("%.<digits>f", x) / Python f"{x:.<digits>f}": correctly rounded from the exact binary value,
 * ties to even. (JS toFixed rounds exact ties up, e.g. (0.0625).toFixed(3) = "0.063" vs C "0.062".)
 */
export function cfix(x: number, digits: number): string {
  if (!Number.isFinite(x)) return Number.isNaN(x) ? 'nan' : x > 0 ? 'inf' : '-inf';
  const neg = x < 0 || Object.is(x, -0);
  const a = Math.abs(x);
  let s = a.toFixed(digits);
  // detect an exact tie: the exact decimal expansion continues with "5" then only zeros
  const long = a.toFixed(Math.min(100, digits + 30));
  const tail = long.slice(long.length - 30);
  if (tail[0] === '5' && /^0*$/.test(tail.slice(1))) {
    // toFixed rounded the tie up; C rounds to even
    const down = long.slice(0, long.length - 30).replace(/\.$/, '');
    const lastDigit = Number(down[down.length - 1]);
    if (lastDigit % 2 === 0) s = down;
  }
  return (neg ? '-' : '') + s;
}

/** Python repr() of a float as used in f"{x}" (e.g. 1.0 -> "1.0", 1.25 -> "1.25"). Integers in JSON stay ints. */
export function pyRepr(x: number, isInt = false): string {
  if (isInt) return String(Math.trunc(x));
  if (Number.isInteger(x) && Math.abs(x) < 1e16) return x.toFixed(1);
  return String(x);
}
