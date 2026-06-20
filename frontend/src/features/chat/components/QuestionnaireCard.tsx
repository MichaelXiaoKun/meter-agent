import { useMemo, useState } from "react";
import type {
  Questionnaire,
  QuestionnaireAnswer,
  QuestionnaireResponse,
} from "../../../core/types";

function hasChoice(
  selected: Record<string, string[]>,
  questionId: string,
  optionId: string,
): boolean {
  return (selected[questionId] ?? []).includes(optionId);
}

function responseFromSelection(
  questionnaire: Questionnaire,
  selected: Record<string, string[]>,
): QuestionnaireResponse {
  const answers: QuestionnaireAnswer[] = questionnaire.questions.map((q) => {
    const optionIds = selected[q.id] ?? [];
    const labels = optionIds
      .map((id) => q.options.find((o) => o.id === id)?.label)
      .filter((label): label is string => !!label);
    return {
      question_id: q.id,
      option_ids: optionIds,
      labels,
    };
  });
  return {
    questionnaire_id: questionnaire.id,
    answers,
  };
}

export function questionnaireResponseSummary(
  questionnaire: Questionnaire,
  response: QuestionnaireResponse,
): string {
  const chinese = questionnaire.questions.some((q) => /[\u3400-\u9fff]/.test(q.text));
  const lines = response.answers.map((answer) => {
    const question = questionnaire.questions.find((q) => q.id === answer.question_id);
    const labels =
      answer.labels && answer.labels.length > 0
        ? answer.labels
        : answer.option_ids
            .map((id) => question?.options.find((o) => o.id === id)?.label ?? id)
            .filter(Boolean);
    return `- ${question?.text ?? answer.question_id}: ${labels.join(", ")}`;
  });
  return `${chinese ? "问卷回答：" : "Questionnaire answers:"}\n${lines.join("\n")}`;
}

interface QuestionnaireCardProps {
  questionnaire: Questionnaire;
  disabled?: boolean;
  answered?: boolean;
  onSubmit?: (response: QuestionnaireResponse, summary: string) => void;
}

export default function QuestionnaireCard({
  questionnaire,
  disabled,
  answered,
  onSubmit,
}: QuestionnaireCardProps) {
  const initial = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const q of questionnaire.questions) {
      out[q.id] = [];
    }
    return out;
  }, [questionnaire.questions]);
  const [selected, setSelected] = useState<Record<string, string[]>>(initial);

  const complete = questionnaire.questions.every((q) => (selected[q.id] ?? []).length > 0);
  const isDisabled = disabled || answered;

  function toggle(questionId: string, optionId: string, multi: boolean) {
    if (isDisabled) return;
    setSelected((prev) => {
      const current = prev[questionId] ?? [];
      const next = multi
        ? current.includes(optionId)
          ? current.filter((id) => id !== optionId)
          : [...current, optionId]
        : [optionId];
      return { ...prev, [questionId]: next };
    });
  }

  function submit() {
    if (!complete || isDisabled) return;
    const response = responseFromSelection(questionnaire, selected);
    onSubmit?.(response, questionnaireResponseSummary(questionnaire, response));
  }

  return (
    <div className="w-full max-w-2xl rounded-lg border border-brand-border bg-white p-4 shadow-sm dark:bg-slate-950">
      <div className="space-y-4">
        {questionnaire.questions.map((q, idx) => {
          const multi = q.type === "multi_choice";
          return (
            <fieldset key={q.id} className="space-y-2">
              <legend className="text-sm font-semibold text-brand-900">
                {idx + 1}. {q.text}
              </legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {q.options.map((option) => {
                  const active = hasChoice(selected, q.id, option.id);
                  return (
                    <button
                      key={option.id}
                      type="button"
                      disabled={isDisabled}
                      onClick={() => toggle(q.id, option.id, multi)}
                      className={`min-h-10 rounded-lg border px-3 py-2 text-left text-sm transition ${
                        active
                          ? "border-brand-600 bg-brand-100 text-brand-900"
                          : "border-brand-border bg-brand-50/40 text-brand-800 hover:border-brand-300 hover:bg-brand-50"
                      } ${isDisabled ? "cursor-not-allowed opacity-70" : ""}`}
                    >
                      {option.label}
                    </button>
                  );
                })}
              </div>
            </fieldset>
          );
        })}
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button
          type="button"
          disabled={!complete || isDisabled}
          onClick={submit}
          className="rounded-lg bg-brand-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {answered ? "Submitted" : "Continue"}
        </button>
      </div>
    </div>
  );
}
