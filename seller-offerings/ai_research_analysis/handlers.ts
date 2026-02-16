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

const DEPTH_INSTRUCTIONS: Record<string, string> = {
  quick: "Provide a concise summary (300-500 words). Key findings, quick assessment, and top-line recommendation.",
  standard: "Provide a detailed analysis (800-1500 words). Include background, key metrics, analysis, risks, and recommendations.",
  deep: "Provide a comprehensive research report (2000+ words). Include executive summary, detailed analysis across multiple dimensions, data points, risk assessment, competitive landscape, and actionable recommendations.",
};

export async function executeJob(request: any): Promise<ExecuteJobResult> {
  const query = request.query;
  const depth = request.depth || "standard";
  const focus = request.focusAreas || "";

  const depthInstr = DEPTH_INSTRUCTIONS[depth] || DEPTH_INSTRUCTIONS.standard;

  const system = `You are a senior research analyst specializing in crypto, AI, and technology markets. Produce well-structured research reports with clear sections, data-driven insights, and actionable conclusions. ${depthInstr}`;
  const prompt = `Research request: ${query}${focus ? `\n\nFocus areas: ${focus}` : ""}`;

  const result = await callClaude(system, prompt);
  return { deliverable: result };
}

export function validateRequirements(request: any): ValidationResult {
  if (!request.query || typeof request.query !== "string" || request.query.trim().length < 5) {
    return { valid: false, reason: "Research query must be at least 5 characters" };
  }
  return { valid: true };
}

export function requestPayment(request: any): string {
  const depth = request.depth || "standard";
  return `Research request received (${depth} depth): "${request.query.slice(0, 80)}". Pay to start analysis.`;
}
