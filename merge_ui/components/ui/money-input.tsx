"use client";

import * as React from "react";

import { Input } from "@/components/ui/input";
import { fmtMoneyInput } from "@/lib/format";

/**
 * A money field that groups thousands while you type.
 *
 * It's a text input, not type="number": browsers refuse to render separators
 * in a number field (and hand back "" for anything they consider invalid), so
 * the stepper arrows are traded for readable amounts. The caret is restored by
 * digit position after each reformat, otherwise inserting a digit in the
 * middle would throw the cursor to the end.
 */
export function MoneyInput({
  value,
  onValueChange,
  ...props
}: Omit<React.ComponentProps<typeof Input>, "value" | "onChange" | "type"> & {
  value: string;
  onValueChange: (formatted: string) => void;
}) {
  const ref = React.useRef<HTMLInputElement>(null);
  const caretDigits = React.useRef<number | null>(null);

  const significant = (s: string) => s.replace(/[^\d.]/g, "").length;

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const el = e.target;
    caretDigits.current = significant(el.value.slice(0, el.selectionStart ?? el.value.length));
    onValueChange(fmtMoneyInput(el.value));
  }

  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el || caretDigits.current === null || document.activeElement !== el) return;
    let seen = 0;
    let pos = 0;
    while (pos < el.value.length && seen < caretDigits.current) {
      if (/[\d.]/.test(el.value[pos])) seen++;
      pos++;
    }
    el.setSelectionRange(pos, pos);
    caretDigits.current = null;
  });

  return (
    <Input
      ref={ref}
      type="text"
      inputMode="decimal"
      value={value}
      onChange={handleChange}
      {...props}
    />
  );
}
