-- Migration: 0010_position_states_trailing_closed
-- Description: Persist trailingClosed flag so trail_tp CLOSE commands are not regenerated
-- on every PM cycle. Without this, the same ticket generates repeated CLOSE commands because
-- the dd value changes slightly → different reason string → different command_id → bypasses
-- minute-level dedupe. This flag is set true when a trail_tp CLOSE advisory is first produced
-- and prevents further trail_tp advisories for the same ticket.

ALTER TABLE position_states ADD COLUMN trailing_closed INTEGER NOT NULL DEFAULT 0;
