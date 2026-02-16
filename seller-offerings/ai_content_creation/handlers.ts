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

const CONTENT_PROMPTS: Record<string, string> = {
  blog_post: "Write a well-structured blog post with an engaging introduction, clear sections with headings, and a conclusion.",
  twitter_thread: "Write a Twitter/X thread. Number each tweet (1/, 2/, etc.). Each tweet must be under 280 characters. Make it engaging and shareable.",
  marketing_copy: "Write persuasive marketing copy. Focus on benefits, use strong CTAs, and maintain brand voice.",
  newsletter: "Write an email newsletter with a compelling subject line, engaging body, and clear call-to-action.",
  whitepaper: "Write a detailed whitepaper with executive summary, problem statement, proposed solution, technical details, and conclusion.",
  documentation: "Write clear technical documentation with proper formatting, code examples where relevant, and step-by-step instructions.",
};

export async function executeJob(request: any): Promise<ExecuteJobResult> {
  const topic = request.topic;
  const contentType = request.contentType || "blog_post";
  const wordCount = request.wordCount || 500;
  const tone = request.tone || "professional";
  const context = request.additionalContext || "";

  const typePrompt = CONTENT_PROMPTS[contentType] || CONTENT_PROMPTS.blog_post;

  const system = `You are a professional content writer. Tone: ${tone}. ${typePrompt} Target length: ~${wordCount} words.`;
  const prompt = `Topic: ${topic}${context ? `\n\nAdditional context: ${context}` : ""}`;

  const result = await callClaude(system, prompt);
  return { deliverable: result };
}

export function validateRequirements(request: any): ValidationResult {
  if (!request.topic || typeof request.topic !== "string" || request.topic.trim().length < 3) {
    return { valid: false, reason: "Topic must be at least 3 characters" };
  }
  return { valid: true };
}

export function requestPayment(request: any): string {
  return `Content creation request received: ${request.contentType || "blog_post"} about "${request.topic.slice(0, 80)}". Pay to start.`;
}
