"use client";

import { InputHTMLAttributes, forwardRef } from "react";

/** A text field with a big "✕" that clears it — the browser's own tiny clear mark of a search field is hidden
 *  (globals.css). ``onClear`` empties the value; the field keeps the focus. */
const ClearInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & {
  value: string; onClear: () => void; wrapperClassName?: string;
}>(function ClearInput({ value, onClear, wrapperClassName = "", className = "", ...rest }, ref) {
  return (
    <div className={`relative ${wrapperClassName}`}>
      <input ref={ref} value={value} {...rest} className={`${className} ${value ? "pr-12" : ""}`} />
      {value && (
        <button type="button" aria-label="Vymazat" title="Vymazat"
          onMouseDown={(e) => e.preventDefault()}
          onClick={onClear}
          className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-lg text-zinc-400 hover:text-zinc-100 active:text-white">
          ✕
        </button>
      )}
    </div>
  );
});

export default ClearInput;
