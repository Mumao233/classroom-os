import type { components } from '../generated/openapi';

export type Feedback = components['schemas']['Feedback'];
export type FeedbackAcknowledgement = components['schemas']['Acknowledged'];

export const feedbackCategories = {
  bug: '功能故障', asr: '转写', ai_content: 'AI 生成内容', ui: '界面',
  sync: '同步', security: '安全', other: '其他',
} satisfies Record<Feedback['category'], string>;

export function feedbackPayload(input: Omit<Feedback, 'confirmed_excerpt' | 'confirm_send_excerpt'>,
                                excerpt: string | null, confirmed: boolean): Feedback {
  const share = confirmed && Boolean(excerpt);
  return {
    category: input.category, build_id: input.build_id, description: input.description.trim(),
    request_id: input.request_id, confirmed_excerpt: share ? excerpt : null, confirm_send_excerpt: share,
  };
}
