"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function SearchBox({
  initial = "",
  size = "lg",
}: {
  initial?: string;
  size?: "lg" | "sm";
}) {
  const router = useRouter();
  const [value, setValue] = useState(initial);
  const big = size === "lg";

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const a = value.trim();
    if (!a) return;
    router.push(`/investigate?address=${encodeURIComponent(a)}`);
  }

  return (
    <form onSubmit={submit} role="search" className="w-full">
      <label htmlFor="address" className="sr-only">
        NYC street address
      </label>
      <div
        className={`flex w-full flex-col gap-2 rounded-2xl border border-stone-300 bg-white p-2 shadow-card focus-within:border-brand sm:flex-row ${big ? "sm:p-2.5" : ""}`}
      >
        <div className="flex flex-1 items-center gap-2 px-2">
          <svg aria-hidden viewBox="0 0 24 24" className="h-5 w-5 shrink-0 text-ink-faint" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
          <input
            id="address"
            name="address"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="e.g. 1134 West Farms Rd, Bronx"
            autoComplete="street-address"
            maxLength={200}
            className={`w-full bg-transparent py-2 outline-none placeholder:text-ink-faint ${big ? "text-lg" : "text-base"}`}
          />
        </div>
        <button type="submit" className="btn-primary" disabled={!value.trim()}>
          Research building
        </button>
      </div>
    </form>
  );
}
