import type { MatchEvent } from "@/types/events";

function categoryStyle(category: MatchEvent["category"]): {
  label: string;
  className: string;
  timeColor: string;
} {
  switch (category) {
    case "match":
      return {
        label: "Match Tactics",
        className:
          "border-brand-accent/30 text-brand-accent bg-brand-accent/10",
        timeColor: "text-brand-accent",
      };
    case "key_action":
      return {
        label: "Key Action",
        className:
          "border-brand-primary/30 text-brand-primary bg-brand-primary/10",
        timeColor: "text-brand-primary",
      };
    case "critical":
      return {
        label: "Critical",
        className:
          "border-brand-success/30 text-brand-success bg-brand-success/10",
        timeColor: "text-brand-success",
      };
    case "card":
      return {
        label: "Card",
        className:
          "border-yellow-500/30 text-yellow-400 bg-yellow-500/10",
        timeColor: "text-yellow-400",
      };
    case "foul":
      return {
        label: "Foul",
        className:
          "border-orange-500/30 text-orange-400 bg-orange-500/10",
        timeColor: "text-orange-400",
      };
    case "set_piece":
      return {
        label: "Set Piece",
        className:
          "border-cyan-500/30 text-cyan-400 bg-cyan-500/10",
        timeColor: "text-cyan-400",
      };
    case "poll":
      return {
        label: "Poll",
        className:
          "border-brand-poll/30 text-brand-poll bg-brand-poll/10",
        timeColor: "text-brand-poll",
      };
    case "sentiment_prompt":
      return {
        label: "Sentiment",
        className:
          "border-brand-sentiment/30 text-brand-sentiment bg-brand-sentiment/10",
        timeColor: "text-brand-sentiment",
      };
    case "product":
      return {
        label: "Recommended",
        className:
          "border-brand-product/30 text-brand-product bg-brand-product/10",
        timeColor: "text-brand-product",
      };
    case "system":
    default:
      return {
        label: "System",
        className: "border-white/20 text-brand-muted bg-white/5",
        timeColor: "text-brand-muted",
      };
  }
}

export function EventItem({ event }: { event: MatchEvent }) {
  const style = categoryStyle(event.category);

  return (
    <div className="event-card event-enter p-3 rounded-lg border border-brand-border bg-brand-panel/30 flex gap-3">
      <div className={`w-12 text-xs font-mono pt-0.5 ${style.timeColor}`}>
        {event.timestamp}
      </div>
      <div className="flex-1">
        <div className="flex justify-between items-start mb-1">
          <h4 className="text-sm font-medium">{event.title}</h4>
          <span
            className={`text-[10px] px-1.5 py-0.5 rounded border ${style.className}`}
          >
            {style.label}
          </span>
        </div>
        {event.description && (
          <p className="text-xs text-brand-muted leading-relaxed">
            {event.description}
          </p>
        )}
      </div>
    </div>
  );
}
