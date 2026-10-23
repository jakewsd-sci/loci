// Stroke icons drawn at 24 x 24, coloured by currentColor.

const paths: Record<string, string[]> = {
  layers: ["m12 3 9 5-9 5-9-5 9-5z", "m3 13 9 5 9-5"],
  code: ["m9 8-4 4 4 4", "m15 8 4 4-4 4"],
  lineage: [
    "M6 3a2 2 0 1 0 0 4 2 2 0 0 0 0-4z",
    "M6 17a2 2 0 1 0 0 4 2 2 0 0 0 0-4z",
    "M18 6a2 2 0 1 0 0 4 2 2 0 0 0 0-4z",
    "M6 7v10",
    "M18 10c0 4.5-6 4-10.5 7.5",
  ],
  agent: ["M4 5h16v11H9l-5 4V5z", "M9 10h6"],
  eye: ["M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z", "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"],
  eyeOff: [
    "M3 3l18 18",
    "M10.6 6.1c.5-.1.9-.1 1.4-.1 6.5 0 10 6 10 6a17 17 0 0 1-3.2 3.8",
    "M6.6 7.6A17 17 0 0 0 2 12s3.5 6 10 6c1.6 0 3-.4 4.4-1",
  ],
  plus: ["M12 5v14", "M5 12h14"],
  minus: ["M5 12h14"],
  fit: ["M4 9V4h5", "M20 9V4h-5", "M4 15v5h5", "M20 15v5h-5"],
  search: ["M11 5a6 6 0 1 0 0 12 6 6 0 0 0 0-12z", "m20 20-4.5-4.5"],
  send: ["M12 19V5", "m6 11 6-6 6 6"],
  play: ["M7 5l12 7-12 7V5z"],
  save: ["M5 4h11l3 3v13H5V4z", "M8 4v5h8", "M8 20v-6h8v6"],
  close: ["M6 6l12 12", "M18 6 6 18"],
  shield: ["M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6l-7-3z", "m9 12 2 2 4-4"],
  file: ["M6 3h8l4 4v14H6V3z", "M14 3v4h4"],
  spark: ["M12 4v4", "M12 16v4", "M4 12h4", "M16 12h4"],
};

export function Icon({ name, size = 18 }: { name: keyof typeof paths; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}

export function Logo() {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="12" cy="12" r="2.6" fill="var(--accent)" />
    </svg>
  );
}
