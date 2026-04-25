import { motion } from "framer-motion";
import { messageVariants, imageVariants } from "../utils/animations";
import MessageBubble from "./MessageBubble";
import type { Message, PlotAttachment } from "../types";

interface AnimatedMessageBubbleProps {
  message: Message;
  plots?: PlotAttachment[];
  transcript?: Message[];
  messageIndex?: number;
}

/**
 * Wrapped MessageBubble with smooth entrance animation
 */
export default function AnimatedMessageBubble({
  message,
  plots,
  transcript,
  messageIndex,
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
        transcript={transcript}
        messageIndex={messageIndex}
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
