const EXAMPLES = [
  "Run a health check on <meter>",
  "Analyse the last 7 days of flow data for <meter>",
  "Is <meter> online and transmitting?",
  "Configure pipe for serial <serial number>: PVC, Schedule 40, 2 inch nominal, transducer angle 45º",
  "Set transducer angle only for serial <serial number> to 35º (no pipe size change)",
];

interface WelcomeCardProps {
  onExampleClick: (text: string) => void;
}

export default function WelcomeCard({ onExampleClick }: WelcomeCardProps) {
  return (
    <div className="mx-auto mt-20 max-w-lg rounded-2xl border border-brand-border bg-linear-to-br from-brand-50 to-brand-100 p-8 text-center shadow-sm">
      <h2 className="mb-1 text-lg font-bold text-brand-900">
        What would you like to do?
      </h2>
      <p className="mb-6 text-sm text-brand-muted">
        For status and flow, use the serial number you have (same string as in the API path).
        Pipe and angle tools use the physical serial number on the enclosure.
      </p>
      <div className="space-y-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            onClick={() => onExampleClick(ex)}
            className="w-full rounded-xl border border-brand-border bg-white px-4 py-3 text-left text-sm text-brand-900 transition-colors hover:border-brand-500 hover:shadow-sm"
          >
            &ldquo;{ex}&rdquo;
          </button>
        ))}
      </div>
    </div>
  );
}
