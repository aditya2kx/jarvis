-- 053: tag scheduled shift rows as shift vs paid PTO (hour_kind)
-- Lets Operator Console filter PTO out of labor charts while default
-- totals still match ADP footer (PTO counts toward labor hours).
-- Values: shift | pto | mixed. NULL on legacy rows treated as shift.

ALTER TABLE `jarvis-bhaga-prod.bhaga.adp_scheduled_shifts`
ADD COLUMN IF NOT EXISTS hour_kind STRING;
