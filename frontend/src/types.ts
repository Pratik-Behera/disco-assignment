export type QuestionMeta = {
  field: string;
  importance: "required" | "useful";
  question: string;
  quick_replies: string[];
  allow_free_text: boolean;
  allow_skip: boolean;
};

export type ChatReply = {
  thread_id: string;
  text: string;
  question?: string;
  question_meta?: QuestionMeta;
};
