// api/mesh.ts
//
// Folds the `mesh` SSE events (gwen-pm t15) into the cards the thread shows (t17):
// an ask card per ask_id (waiting / answered / expired), an answer card, and an
// agent-contacted card. Pure: the same events give the same cards. `text` on an answer
// or a contact is ANOTHER AGENT'S WORDS (untrusted); nothing here interprets it.
import type { EventEnvelope } from "./events";

export type AskState = "waiting" | "answered" | "expired";

export interface AskCard {
  type: "ask";
  key: string;
  askId: string;
  state: AskState;
  conversationId: string;
  channel: string | null;
  question: string;
  at: number;
  expiresAt: number | null;
}

export interface AnswerCard {
  type: "answer";
  key: string;
  askId: string;
  conversationId: string;
  nick: string;
  channel: string | null;
  text: string;
  at: number;
  /** Seconds after the ask was posted, when the ask is known. */
  afterS: number | null;
  late: boolean;
  afterExpiry: boolean;
  ambiguous: boolean;
}

export interface ContactCard {
  type: "contact";
  key: string;
  conversationId: string;
  nick: string;
  channel: string | null;
  direct: boolean;
  text: string;
  at: number;
}

export type MeshCard = AskCard | AnswerCard | ContactCard;

type Mesh = EventEnvelope<"mesh">;

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const nul = (v: unknown): string | null => (typeof v === "string" ? v : null);
const num = (v: unknown, d = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : d);

export function foldMesh(events: readonly Mesh[]): MeshCard[] {
  const cards: MeshCard[] = [];
  const asks = new Map<string, AskCard>();
  for (const e of events) {
    const d = e.data;
    const conversationId = str(d.conversation_id);
    const at = num(d.at);
    const askId = str(d.ask_id);
    switch (d.event) {
      case "ask_posted": {
        const card: AskCard = {
          type: "ask", key: `ask-${e.seq}`, askId, state: "waiting", conversationId,
          channel: nul(d.channel), question: str(d.text), at,
          expiresAt: typeof d.expires_at === "number" ? d.expires_at : null,
        };
        if (askId) asks.set(askId, card);
        cards.push(card);
        break;
      }
      case "answer_arrived": {
        const ask = asks.get(askId);
        if (ask) ask.state = "answered";
        cards.push({
          type: "answer", key: `answer-${e.seq}`, askId, conversationId,
          nick: str(d.nick), channel: nul(d.channel), text: str(d.text), at,
          afterS: ask ? Math.max(0, Math.round(at - ask.at)) : null,
          late: d.late === true, afterExpiry: d.after_expiry === true,
          ambiguous: d.ambiguous === true && d.rule === "fallback",
        });
        break;
      }
      case "ask_expired": {
        const ask = asks.get(askId);
        if (ask && ask.state === "waiting") ask.state = "expired";
        break;
      }
      case "agent_contacted":
        cards.push({
          type: "contact", key: `contact-${e.seq}`, conversationId, nick: str(d.nick),
          channel: nul(d.channel), direct: d.direct === true, text: str(d.text), at,
        });
        break;
      default:
        break; // gwen_replied is already Gwen's own reply in the thread
    }
  }
  return cards;
}

/** Asks posted and neither answered nor expired. */
export function openAskCount(events: readonly Mesh[]): number {
  return foldMesh(events).filter((c) => c.type === "ask" && c.state === "waiting").length;
}

/**
 * The cards for one conversation. `null` is the live view: every card except those of
 * conversations another agent started with Gwen (those open from the list); the
 * agent-contacted card stays visible so the operator sees who reached her.
 */
export function meshCardsFor(events: readonly Mesh[], conversationId: string | null): MeshCard[] {
  const cards = foldMesh(events);
  if (conversationId !== null) return cards.filter((c) => c.conversationId === conversationId);
  const agentConversations = new Set(cards.filter((c) => c.type === "contact").map((c) => c.conversationId));
  return cards.filter((c) => c.type === "contact" || !agentConversations.has(c.conversationId));
}
