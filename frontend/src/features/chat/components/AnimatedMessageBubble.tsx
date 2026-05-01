import { motion } from "framer-motion";
import { messageVariants, imageVariants } from "../../../utils/animations";
import MessageBubble from "./MessageBubble";
import type { DownloadArtifact, Message, PlotAttachment, SSEEvent } from "../../../core/types";

type ConfigWorkflow = NonNullable<SSEEvent["config_workflow"]>;
type ToastFn = (a: {
  kind: "success" | "error";
  title: string;
  message?: string;
}) => void;

interface AnimatedMessageBubbleProps {
  message: Message;
  plots?: PlotAttachment[];
  artifacts?: DownloadArtifact[];
  transcript?: Message[];
  messageIndex?: number;
  onConfirmConfig?: (workflow: ConfigWorkflow) => void;
  onCancelConfig?: (workflow: ConfigWorkflow) => void;
  onTypeOtherConfig?: (workflow: ConfigWorkflow) => void;
  configActionsDisabled?: boolean;
  liveConfigEvents?: SSEEvent[];
  accessToken?: string | null;
  anthropicApiKey?: string | null;
  onToast?: ToastFn;
}

/**
 * Wrapped MessageBubble with smooth entrance animation
 */
export default function AnimatedMessageBubble({
  message,
  plots,
  artifacts,
  transcript,
  messageIndex,
  onConfirmConfig,
  onCancelConfig,
  onTypeOtherConfig,
  configActionsDisabled,
  liveConfigEvents,
  accessToken,
  anthropicApiKey,
  onToast,
}: AnimatedMessageBubbleProps) {
  return (
    <motion.div
      initial="hidden"
      animate="visible"
      exit="exit"
      variants={messageVariants}
      layout
    >
      <MessageBubble
        message={message}
        plots={plots}
        artifacts={artifacts}
        transcript={transcript}
        messageIndex={messageIndex}
        onConfirmConfig={onConfirmConfig}
        onCancelConfig={onCancelConfig}
        onTypeOtherConfig={onTypeOtherConfig}
        configActionsDisabled={configActionsDisabled}
        liveConfigEvents={liveConfigEvents}
        accessToken={accessToken}
        anthropicApiKey={anthropicApiKey}
        onToast={onToast}
      />
    </motion.div>
  );
}

/**
 * Animated plot image with fade-in
 */
export function AnimatedPlotImage({
  src,
  alt,
  title,
  className,
}: {
  src: string;
  alt?: string;
  title?: string;
  className?: string;
}) {
  return (
    <motion.div
      initial="hidden"
      whileInView="visible"
      viewport={{ once: true, amount: 0.1 }}
      variants={imageVariants}
    >
      <img
        src={src}
        alt={alt}
        title={title}
        className={className}
        loading="lazy"
      />
    </motion.div>
  );
}
