/**
 * Optional peer packages — not ClawChips dependencies; declared for TS only.
 */
declare module "@openclaw/qqbot/api.js" {
  export function sendProactive(
    options: {
      to: string;
      text: string;
      type?: "c2c" | "group";
      accountId?: string;
      imageUrl?: string;
    },
    cfg: Record<string, unknown>,
  ): Promise<{ success: boolean; error?: string; messageId?: string; timestamp?: number | string }>;
}

declare module "@openclaw/qqbot/api" {
  export function sendProactive(
    options: {
      to: string;
      text: string;
      type?: "c2c" | "group";
      accountId?: string;
      imageUrl?: string;
    },
    cfg: Record<string, unknown>,
  ): Promise<{ success: boolean; error?: string; messageId?: string; timestamp?: number | string }>;
}

/** Tencent global extension `@tencent-connect/openclaw-qqbot` — proactive is not on package main. */
declare module "@tencent-connect/openclaw-qqbot/dist/src/proactive.js" {
  export function sendProactive(
    options: {
      to: string;
      text: string;
      type?: "c2c" | "group";
      accountId?: string;
      imageUrl?: string;
    },
    cfg: Record<string, unknown>,
  ): Promise<{ success: boolean; error?: string; messageId?: string; timestamp?: number | string }>;
}

declare module "@tencent-connect/openclaw-qqbot/dist/src/proactive" {
  export function sendProactive(
    options: {
      to: string;
      text: string;
      type?: "c2c" | "group";
      accountId?: string;
      imageUrl?: string;
    },
    cfg: Record<string, unknown>,
  ): Promise<{ success: boolean; error?: string; messageId?: string; timestamp?: number | string }>;
}
