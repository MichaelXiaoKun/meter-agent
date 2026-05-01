import { motion } from "framer-motion";
import { containerVariants } from "../../../utils/animations";
import TurnActivityTimeline from "./TurnActivityTimeline";
import type { TurnActivityStep } from "../../../core/turnActivity";

interface AnimatedTurnActivityTimelineProps {
  steps: TurnActivityStep[];
  active: boolean;
  announce?: boolean;
}

/**
 * Wrapped TurnActivityTimeline with staggered animations for each step
 */
export default function AnimatedTurnActivityTimeline({
  steps,
  active,
  announce = true,
}: AnimatedTurnActivityTimelineProps) {
  return (
    <motion.div
      variants={containerVariants}
      initial="hidden"
      animate="visible"
    >
      <TurnActivityTimeline
        steps={steps}
        active={active}
        announce={announce}
      />
    </motion.div>
  );
}
