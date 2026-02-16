import type { ExecuteJobResult, ValidationResult } from "../../runtime/offeringTypes.js";

const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY || "";
const CLAUDE_MODEL = "claude-sonnet-4-5-20250929";

async function callClaude(system: string, prompt: string): Promise<string> {
  const resp = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: CLAUDE_MODEL,
      max_tokens: 4096,
      system,
      messages: [{ role: "user", content: prompt }],
    }),
  });
  const data = await resp.json() as any;
  return data.content?.[0]?.text ?? "No response generated";
}

export async function executeJob(request: any): Promise<ExecuteJobResult> {
  const task = request.task || request.request || JSON.stringify(request);
  const format = request.format || "text";

  const system = `You are an AI task execution agent. Complete the requested task thoroughly and professionally. Output format: ${format}. Be concise but comprehensive.`;

  const result = await callClaude(system, task);

  const maxLen = request.maxLength || 10000;
  const trimmed = result.length > maxLen ? result.slice(0, maxLen) + "\n\n[truncated]" : result;

  return { deliverable: trimmed };
}

export function validateRequirements(request: any): ValidationResult {
  const task = request.task || request.request;
  if (!task || typeof task !== "string" || task.trim().length < 5) {
    return { valid: false, reason: "Task description must be at least 5 characters" };
  }
  return { valid: true };
}

export function requestPayment(request: any): string {
  return `Task received: "${(request.task || request.request || "").slice(0, 100)}". Please proceed with payment to start execution.`;
}
