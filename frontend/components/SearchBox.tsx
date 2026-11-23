"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowRight, Loader2, MapPin } from "lucide-react";

export default function SearchBox({
  initial = "",
  size = "lg",
  autoFocus = false,
}: {
  initial?: string;
  size?: "lg" | "sm";
  autoFocus?: boolean;
}) {
  const router = useRouter();
  const [value, setValue] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const big = size === "lg";

  useEffect(() => setBusy(false), [initial]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const a = value.trim();
    if (a.length < 4 || !/\d/.test(a)) {
      setError("Enter a street address with a house number, for example “1134 West Farms Rd, Bronx”.");
      return;
    }
    setError(null);
    if (a === initial.trim()) return;
    setBusy(true);
    router.push(`/investigate?address=${encodeURIComponent(a)}`);
  }

  return (
    <form onSubmit={submit} role="search" className="w-full" noValidate>
      <label htmlFor={big ? "address" : "address-sm"} className="sr-only">
        NYC street address
      </label>
      <div
        className={`flex w-full flex-col gap-2 rounded-2xl border bg-white p-2 transition focus-within:border-brand focus-within:ring-4 focus-within:ring-brand/10 sm:flex-row sm:items-center ${
          error ? "border-caution" : "border-line"
        } ${big ? "shadow-lift sm:p-2.5" : "shadow-card"}`}
      >
        <div className="flex min-w-0 flex-1 items-center gap-3 px-3">
          <MapPin aria-hidden className={`shrink-0 text-brand ${big ? "h-6 w-6" : "h-5 w-5"}`} />
          <input
            id={big ? "address" : "address-sm"}
            name="address"
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              if (error) setError(null);
            }}
            placeholder="e.g. 220 W 17th St, Manhattan"
            autoComplete="street-address"
            autoFocus={autoFocus}
            maxLength={200}
            aria-invalid={!!error}
            aria-describedby={error ? "address-error" : undefined}
            className={`w-full min-w-0 bg-transparent outline-none placeholder:text-ink-faint/80 ${
              big ? "py-3 text-lg sm:text-xl" : "py-2 text-base"
            }`}
          />
        </div>
        <button
          type="submit"
          className={`btn-primary shrink-0 ${big ? "px-6 py-3.5 text-base" : "px-5 py-2.5"}`}
          disabled={busy}
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
          {busy ? "Researching…" : "Research building"}
          {!busy && <ArrowRight className="h-4 w-4" aria-hidden />}
        </button>
      </div>
      {error && (
        <p id="address-error" className="mt-2 px-1 text-sm text-caution" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
