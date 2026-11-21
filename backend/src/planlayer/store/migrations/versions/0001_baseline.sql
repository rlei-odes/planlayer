-- Planlayer's app store at 0.5.0, the baseline every installation starts from (migration 0001).
-- __SCHEMA__ stands for the configured app schema. Later changes are migrations of their own;
-- this file is never edited.

CREATE FUNCTION __SCHEMA__.audit_fact_capacity() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO "__SCHEMA__".capacity_change_log (actor, batch_id, op, version_id, period, dim_cost_center, dim_work_center, dim_activity_type, old_amount, new_amount)
                SELECT nullif(current_setting('app.actor', true), ''), nullif(current_setting('app.batch_id', true), ''), 'INSERT', version_id, period, dim_cost_center, dim_work_center, dim_activity_type, NULL, amount
                FROM new_rows WHERE version_id IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind <> 'actuals');
            ELSIF TG_OP = 'UPDATE' THEN
                INSERT INTO "__SCHEMA__".capacity_change_log (actor, batch_id, op, version_id, period, dim_cost_center, dim_work_center, dim_activity_type, old_amount, new_amount)
                SELECT nullif(current_setting('app.actor', true), ''), nullif(current_setting('app.batch_id', true), ''), 'UPDATE', n.version_id, n.period, n.dim_cost_center,
                       n.dim_work_center, n.dim_activity_type, o.amount, n.amount
                FROM new_rows n JOIN old_rows o USING (version_id, layer_id, measure_id, period,
                    dim_entity, dim_cost_center, dim_work_center, dim_activity_type)
                WHERE n.version_id IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind <> 'actuals') AND n.amount IS DISTINCT FROM o.amount;
            ELSE
                INSERT INTO "__SCHEMA__".capacity_change_log (actor, batch_id, op, version_id, period, dim_cost_center, dim_work_center, dim_activity_type, old_amount, new_amount)
                SELECT nullif(current_setting('app.actor', true), ''), nullif(current_setting('app.batch_id', true), ''), 'DELETE', version_id, period, dim_cost_center, dim_work_center, dim_activity_type, amount, NULL
                FROM old_rows WHERE version_id IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind <> 'actuals');
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.audit_fact_cost_center() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO "__SCHEMA__".change_log (actor, batch_id, op, version_id, period, dim_cost_center, dim_account, old_amount, new_amount)
                SELECT nullif(current_setting('app.actor', true), ''), nullif(current_setting('app.batch_id', true), ''), 'INSERT', version_id, period, dim_cost_center, dim_account, NULL, amount
                FROM new_rows WHERE version_id IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind <> 'actuals');
            ELSIF TG_OP = 'UPDATE' THEN
                INSERT INTO "__SCHEMA__".change_log (actor, batch_id, op, version_id, period, dim_cost_center, dim_account, old_amount, new_amount)
                SELECT nullif(current_setting('app.actor', true), ''), nullif(current_setting('app.batch_id', true), ''), 'UPDATE', n.version_id, n.period, n.dim_cost_center,
                       n.dim_account, o.amount, n.amount
                FROM new_rows n JOIN old_rows o USING (version_id, layer_id, measure_id, period,
                                                      dim_entity, dim_cost_center, dim_account)
                WHERE n.version_id IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind <> 'actuals') AND n.amount IS DISTINCT FROM o.amount;
            ELSE
                INSERT INTO "__SCHEMA__".change_log (actor, batch_id, op, version_id, period, dim_cost_center, dim_account, old_amount, new_amount)
                SELECT nullif(current_setting('app.actor', true), ''), nullif(current_setting('app.batch_id', true), ''), 'DELETE', version_id, period, dim_cost_center, dim_account, amount, NULL
                FROM old_rows WHERE version_id IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind <> 'actuals');
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.audit_fact_workforce() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO "__SCHEMA__".workforce_change_log (actor, batch_id, op, version_id, measure_id, period, dim_cost_center, dim_staff_type, dim_staff_category, old_amount, new_amount)
                SELECT nullif(current_setting('app.actor', true), ''), nullif(current_setting('app.batch_id', true), ''), 'INSERT', version_id, measure_id, period, dim_cost_center, dim_staff_type, dim_staff_category, NULL, amount
                FROM new_rows WHERE version_id IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind <> 'actuals');
            ELSIF TG_OP = 'UPDATE' THEN
                INSERT INTO "__SCHEMA__".workforce_change_log (actor, batch_id, op, version_id, measure_id, period, dim_cost_center, dim_staff_type, dim_staff_category, old_amount, new_amount)
                SELECT nullif(current_setting('app.actor', true), ''), nullif(current_setting('app.batch_id', true), ''), 'UPDATE', n.version_id, n.measure_id, n.period,
                       n.dim_cost_center, n.dim_staff_type, n.dim_staff_category,
                       o.amount, n.amount
                FROM new_rows n JOIN old_rows o USING (version_id, layer_id, measure_id, period,
                    dim_entity, dim_cost_center, dim_staff_type, dim_staff_category)
                WHERE n.version_id IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind <> 'actuals') AND n.amount IS DISTINCT FROM o.amount;
            ELSE
                INSERT INTO "__SCHEMA__".workforce_change_log (actor, batch_id, op, version_id, measure_id, period, dim_cost_center, dim_staff_type, dim_staff_category, old_amount, new_amount)
                SELECT nullif(current_setting('app.actor', true), ''), nullif(current_setting('app.batch_id', true), ''), 'DELETE', version_id, measure_id, period, dim_cost_center, dim_staff_type, dim_staff_category, amount, NULL
                FROM old_rows WHERE version_id IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind <> 'actuals');
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.capex_item_rules() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            me int := nullif(current_setting('app.user_id', true), '')::int;
            writes boolean;
            confirms boolean;
            content boolean := false;
        BEGIN
            SELECT EXISTS (SELECT 1 FROM "__SCHEMA__".cost_center_access
                           WHERE user_id = me AND cost_center_id = NEW.cost_center_id
                             AND can_write),
                   EXISTS (SELECT 1 FROM "__SCHEMA__".capex_confirmer
                           WHERE user_id = me AND cost_center_id = NEW.cost_center_id)
            INTO writes, confirms;
            IF NEW.project_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM "__SCHEMA__".capex_item WHERE item_id = NEW.project_id AND kind = 'project'
            ) THEN
                RAISE EXCEPTION 'an investment can only be part of a large project'
                    USING ERRCODE = 'PL003';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF coalesce(NEW.status, 'open') <> 'open' AND NOT confirms THEN
                    RAISE EXCEPTION 'only an approver above the cost center or a controller '
                        'confirms investments' USING ERRCODE = 'PL003';
                END IF;
                RETURN NEW;
            END IF;
            content := (NEW.amount, NEW.start_month, NEW.type_id, NEW.cost_center_id,
                        NEW.project_id, NEW.kind)
                       IS DISTINCT FROM
                       (OLD.amount, OLD.start_month, OLD.type_id, OLD.cost_center_id,
                        OLD.project_id, OLD.kind);
            -- Back to open, or into a project, is anyone's who writes; a confirmation isn't.
            IF NEW.status IS DISTINCT FROM OLD.status
               AND coalesce(NEW.status, 'open') <> 'open' AND NOT confirms THEN
                RAISE EXCEPTION 'only an approver above the cost center or a controller '
                    'confirms investments' USING ERRCODE = 'PL003';
            END IF;
            IF NOT writes AND (content OR (NEW.name, NEW.priority)
                                          IS DISTINCT FROM (OLD.name, OLD.priority)) THEN
                RAISE EXCEPTION 'investments are changed by those who plan the cost center'
                    USING ERRCODE = 'PL003';
            END IF;
            IF content AND NEW.status IS NOT DISTINCT FROM OLD.status
               AND NEW.status IS DISTINCT FROM 'open' AND NEW.status IS NOT NULL THEN
                NEW.status := 'open';
                NEW.status_by := NULL;
                NEW.status_at := NULL;
            END IF;
            IF to_jsonb(NEW) - 'changed_at' - 'changed_by' - 'row_version'
               IS DISTINCT FROM to_jsonb(OLD) - 'changed_at' - 'changed_by' - 'row_version' THEN
                NEW.row_version := OLD.row_version + 1;
            END IF;
            RETURN NEW;
        END $$;

CREATE FUNCTION __SCHEMA__.capex_reopen_project() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            projects int[];
        BEGIN
            IF TG_OP = 'INSERT' THEN
                projects := ARRAY[NEW.project_id];
            ELSIF TG_OP = 'DELETE' THEN
                projects := ARRAY[OLD.project_id];
            ELSIF (NEW.amount, NEW.start_month, NEW.type_id, NEW.project_id)
                  IS DISTINCT FROM (OLD.amount, OLD.start_month, OLD.type_id, OLD.project_id) THEN
                projects := ARRAY[OLD.project_id, NEW.project_id];
            ELSE
                RETURN NULL;
            END IF;
            UPDATE "__SCHEMA__".capex_item SET status = 'open', status_by = NULL, status_at = NULL
            WHERE item_id = ANY (projects) AND status <> 'open';
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.check_capex_cash_out() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            i record;
            scheduled numeric;
        BEGIN
            SELECT kind, amount INTO i FROM "__SCHEMA__".capex_item WHERE item_id = NEW.item_id;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            SELECT coalesce(sum(amount), 0) INTO scheduled
            FROM "__SCHEMA__".capex_cash_out WHERE item_id = NEW.item_id;
            IF scheduled > 0 AND i.kind <> 'line' THEN
                RAISE EXCEPTION 'a large project''s cash out is its lines'''
                    USING ERRCODE = 'PL003';
            END IF;
            IF scheduled > coalesce(i.amount, 0) THEN
                RAISE EXCEPTION 'cash out of % is more than the investment''s amount', scheduled
                    USING ERRCODE = 'PL003';
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.guard_forwarding() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            vid smallint;
            st text;
        BEGIN
            IF TG_TABLE_NAME = 'forwarding' THEN
                vid := CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
            ELSE
                SELECT f.version_id INTO vid FROM "__SCHEMA__".forwarding f WHERE f.forwarding_id =
                    CASE WHEN TG_OP = 'DELETE' THEN OLD.forwarding_id ELSE NEW.forwarding_id END;
                IF vid IS NULL THEN  -- deleted with its forwarding
                    RETURN NULL;
                END IF;
            END IF;
            IF NOT "__SCHEMA__".writes_as_controller() THEN
                RAISE EXCEPTION 'plan values are locked: only controllers change cost forwarding'
                    USING ERRCODE = 'PL001';
            END IF;
            SELECT status INTO st FROM "__SCHEMA__".planning_round WHERE version_id = vid FOR SHARE;
            IF st NOT IN ('setup', 'open') THEN
                RAISE EXCEPTION 'plan values are locked: the planning round is %', st
                    USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.guard_item_list() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF (NEW.visibility IS DISTINCT FROM OLD.visibility
                OR NEW.owner_id IS DISTINCT FROM OLD.owner_id)
               AND OLD.owner_id IS DISTINCT FROM nullif(current_setting('app.user_id', true), '')::int THEN
                RAISE EXCEPTION 'only the list''s owner changes its visibility'
                    USING ERRCODE = 'PL002';
            END IF;
            RETURN NEW;
        END $$;

CREATE FUNCTION __SCHEMA__.guard_pay_review() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF NOT "__SCHEMA__".writes_as_hr() THEN
                RAISE EXCEPTION 'plan values are locked: only HR confirms pay'
                    USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.guard_pay_rights() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF NOT ("__SCHEMA__".writes_as_hr() OR EXISTS (
                    SELECT 1 FROM "__SCHEMA__".app_user u WHERE u.user_id = nullif(current_setting('app.user_id', true), '')::int AND u.is_system)) THEN
                RAISE EXCEPTION 'plan values are locked: only HR changes who reads pay'
                    USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.guard_pay_rules() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            vid smallint;
            st text;
        BEGIN
            IF TG_TABLE_NAME = 'pay_rule_step' THEN
                SELECT r.version_id INTO vid FROM "__SCHEMA__".pay_rule r WHERE r.rule_id =
                    CASE WHEN TG_OP = 'DELETE' THEN OLD.rule_id ELSE NEW.rule_id END;
                IF vid IS NULL THEN  -- deleted with its rule
                    RETURN NULL;
                END IF;
            ELSE
                vid := CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
            END IF;
            IF NOT ("__SCHEMA__".writes_as_hr() OR "__SCHEMA__".writes_as_controller()) THEN
                RAISE EXCEPTION 'plan values are locked: only HR and controllers change the '
                    'personnel cost rules' USING ERRCODE = 'PL001';
            END IF;
            SELECT status INTO st FROM "__SCHEMA__".planning_round WHERE version_id = vid FOR SHARE;
            IF st NOT IN ('setup', 'open') THEN
                RAISE EXCEPTION 'plan values are locked: the planning round is %', st
                    USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.guard_pay_step() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            st text;
        BEGIN
            IF EXISTS (SELECT 1 FROM "__SCHEMA__".app_user u
                       WHERE u.user_id = nullif(current_setting('app.user_id', true), '')::int
                         AND u.is_system) THEN
                RETURN NULL;
            END IF;
            IF NOT "__SCHEMA__".writes_as_hr() THEN
                RAISE EXCEPTION 'plan values are locked: only HR does this'
                    USING ERRCODE = 'PL001';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN NULL;
            END IF;
            SELECT status INTO st FROM "__SCHEMA__".planning_round WHERE version_id = NEW.version_id;
            IF st <> 'open' THEN
                RAISE EXCEPTION 'plan values are locked: the controller opens the round first'
                    USING ERRCODE = 'PL001';
            END IF;
            IF TG_TABLE_NAME = 'pay_handover' AND NOT EXISTS (
                    SELECT 1 FROM "__SCHEMA__".pay_release x WHERE x.version_id = NEW.version_id) THEN
                RAISE EXCEPTION 'plan values are locked: HR releases access control first'
                    USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.guard_workforce_position() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF OLD.origin <> 'hr' THEN
                RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            END IF;
            IF TG_OP = 'DELETE' THEN
                IF NOT "__SCHEMA__".writes_as_controller() THEN
                    RAISE EXCEPTION 'plan values are locked: only controllers remove a position '
                        'taken from HR' USING ERRCODE = 'PL001';
                END IF;
                RETURN OLD;
            END IF;
            IF (NEW.origin, NEW.code, NEW.person_name, NEW.entry_date, NEW.is_plan,
                NEW.is_replacement) IS DISTINCT FROM
               (OLD.origin, OLD.code, OLD.person_name, OLD.entry_date, OLD.is_plan,
                OLD.is_replacement) THEN
                RAISE EXCEPTION 'plan values are locked: a position taken from HR keeps its '
                    'number, name and entry date' USING ERRCODE = 'PL001';
            END IF;
            RETURN NEW;
        END $$;

CREATE FUNCTION __SCHEMA__.lock_capacity_line() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            l "__SCHEMA__".capacity_line;
            problem text;
        BEGIN
            l := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            PERFORM 1 FROM "__SCHEMA__".planning_round WHERE version_id = l.version_id FOR SHARE;
            
        SELECT CASE
                   WHEN r.version_id IS NULL THEN 'the version has no planning round'
                   WHEN NOT (r.status = 'open' OR "__SCHEMA__".prepares(r.status)) THEN 'the planning round is ' || r.status
                   WHEN NOT coalesce(m.enabled, false)
                       THEN 'the cost center is not productive in the round'
                   WHEN coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress')
                       THEN 'the cost center is ' || w.status
               END
        INTO problem
        FROM (SELECT l.version_id, l.cost_center_id) AS t
        LEFT JOIN "__SCHEMA__".planning_round r ON r.version_id = t.version_id
        LEFT JOIN "__SCHEMA__".round_member m
            ON m.version_id = t.version_id AND m.module = 'capacity'
            AND m.member_id = t.cost_center_id
        LEFT JOIN "__SCHEMA__".workflow_status w
            ON w.version_id = t.version_id AND w.cost_center_id = t.cost_center_id
        WHERE r.version_id IS NULL OR NOT (r.status = 'open' OR "__SCHEMA__".prepares(r.status)) OR NOT coalesce(m.enabled, false)
           OR coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress')
        LIMIT 1;
        IF problem IS NOT NULL THEN
            RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
        END IF;
    
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.lock_capacity_staff() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            l record;
            problem text;
        BEGIN
            IF TG_OP = 'DELETE' THEN l := OLD; ELSE l := NEW; END IF;
            SELECT CASE
                       WHEN NOT (r.status = 'open' OR "__SCHEMA__".prepares(r.status)) THEN 'the planning round is ' || r.status
                       WHEN NOT coalesce(m.enabled, false)
                           THEN 'the cost center is not productive in the round'
                       WHEN coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress')
                           THEN 'the cost center is ' || w.status
                   END
            INTO problem
            FROM "__SCHEMA__".planning_round r
            LEFT JOIN "__SCHEMA__".round_member m
                ON m.version_id = r.version_id AND m.module = 'capacity'
                AND m.member_id = l.cost_center_id
            LEFT JOIN "__SCHEMA__".workflow_status w
                ON w.version_id = r.version_id AND w.cost_center_id = l.cost_center_id
            WHERE r.version_id = l.version_id
            FOR SHARE OF r;
            IF problem IS NOT NULL THEN
                RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.lock_capex() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            row_item int;
            v smallint;
            cc int;
            problem text;
        BEGIN
            IF TG_TABLE_NAME = 'capex_item' THEN
                IF TG_OP = 'DELETE' THEN v := OLD.version_id; cc := OLD.cost_center_id;
                ELSE v := NEW.version_id; cc := NEW.cost_center_id; END IF;
            ELSE
                IF TG_OP = 'DELETE' THEN row_item := OLD.item_id;
                ELSE row_item := NEW.item_id; END IF;
                SELECT i.version_id, i.cost_center_id INTO v, cc
                FROM "__SCHEMA__".capex_item i WHERE i.item_id = row_item;
                IF NOT FOUND THEN
                    RETURN NULL;  -- the investment itself was deleted, and checked
                END IF;
            END IF;
            SELECT CASE
                       WHEN NOT (r.status = 'open' OR "__SCHEMA__".prepares(r.status)) THEN 'the planning round is ' || r.status
                       WHEN NOT coalesce(m.enabled, false)
                           THEN 'the cost center is not planned in the round'
                   END
            INTO problem
            FROM "__SCHEMA__".planning_round r
            LEFT JOIN "__SCHEMA__".round_member m
                ON m.version_id = r.version_id AND m.module = 'cost_center'
                AND m.member_id = cc
            WHERE r.version_id = v
            FOR SHARE OF r;
            IF problem IS NOT NULL THEN
                RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.lock_closed_months() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM new_rows t
                JOIN "__SCHEMA__".planning_round r ON r.version_id = t.version_id
                WHERE t.period <= r.actuals_through
            ) THEN
                RAISE EXCEPTION 'plan values are locked: the month is closed in this forecast'
                    USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.lock_fact_capacity() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            v smallint[];
            c int[];
            problem text;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                SELECT array_agg(version_id), array_agg(dim_cost_center) INTO v, c FROM (SELECT DISTINCT version_id, dim_cost_center FROM new_rows WHERE version_id NOT IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind = 'actuals')) x;
            ELSIF TG_OP = 'UPDATE' THEN
                SELECT array_agg(version_id), array_agg(dim_cost_center) INTO v, c FROM (SELECT DISTINCT version_id, dim_cost_center FROM (SELECT * FROM new_rows UNION ALL SELECT * FROM old_rows) u WHERE version_id NOT IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind = 'actuals')) x;
            ELSE
                SELECT array_agg(version_id), array_agg(dim_cost_center) INTO v, c FROM (SELECT DISTINCT version_id, dim_cost_center FROM old_rows WHERE version_id NOT IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind = 'actuals')) x;
            END IF;
            IF v IS NULL THEN
                RETURN NULL;
            END IF;
            PERFORM 1 FROM "__SCHEMA__".planning_round WHERE version_id = ANY (v) FOR SHARE;
            INSERT INTO "__SCHEMA__".workflow_status (version_id, cost_center_id, status)
                SELECT DISTINCT t.version_id, t.cost_center_id, 'not_started' FROM unnest(v, c) AS t(version_id, cost_center_id)
                WHERE t.version_id IN (SELECT version_id FROM "__SCHEMA__".planning_round)
                ON CONFLICT DO NOTHING;
            PERFORM 1 FROM "__SCHEMA__".workflow_status w
                WHERE (w.version_id, w.cost_center_id) IN
                      (SELECT t.version_id, t.cost_center_id FROM unnest(v, c) AS t(version_id, cost_center_id))
                FOR UPDATE;
            
        SELECT CASE
                   WHEN r.version_id IS NULL THEN 'the version has no planning round'
                   WHEN NOT (r.status = 'open' OR "__SCHEMA__".prepares(r.status)) THEN 'the planning round is ' || r.status
                   WHEN NOT coalesce(m.enabled, false)
                       THEN 'the cost center is not productive in the round'
                   WHEN coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress')
                       THEN 'the cost center is ' || w.status
               END
        INTO problem
        FROM unnest(v, c) AS t(version_id, cost_center_id)
        LEFT JOIN "__SCHEMA__".planning_round r ON r.version_id = t.version_id
        LEFT JOIN "__SCHEMA__".round_member m
            ON m.version_id = t.version_id AND m.module = 'capacity'
            AND m.member_id = t.cost_center_id
        LEFT JOIN "__SCHEMA__".workflow_status w
            ON w.version_id = t.version_id AND w.cost_center_id = t.cost_center_id
        WHERE r.version_id IS NULL OR NOT (r.status = 'open' OR "__SCHEMA__".prepares(r.status)) OR NOT coalesce(m.enabled, false)
           OR coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress')
        LIMIT 1;
        IF problem IS NOT NULL THEN
            RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
        END IF;
    
            IF TG_OP <> 'DELETE' THEN
                WITH started AS (
                    UPDATE "__SCHEMA__".workflow_status w
                    SET status = 'in_progress', changed_at = now(),
                        changed_by = nullif(current_setting('app.actor', true), '')
                    WHERE w.status = 'not_started'
                      AND (w.version_id, w.cost_center_id) IN
                          (SELECT t.version_id, t.cost_center_id FROM unnest(v, c) AS t(version_id, cost_center_id))
                      AND w.version_id IN (SELECT version_id FROM "__SCHEMA__".planning_round WHERE status = 'open')
                    RETURNING w.version_id, w.cost_center_id
                )
                INSERT INTO "__SCHEMA__".workflow_event
                    (version_id, cost_center_id, actor, from_status, to_status)
                SELECT version_id, cost_center_id,
                       nullif(current_setting('app.actor', true), ''),
                       'not_started', 'in_progress'
                FROM started;
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.lock_fact_cost_center() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            v smallint[];
            c int[];
            a int[];
            problem text;
            controller boolean := "__SCHEMA__".writes_as_controller();
        BEGIN
            IF TG_OP = 'INSERT' THEN
                SELECT array_agg(version_id), array_agg(dim_cost_center), array_agg(dim_account) INTO v, c, a FROM (SELECT DISTINCT version_id, dim_cost_center, dim_account FROM new_rows WHERE version_id NOT IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind = 'actuals')) x;
            ELSIF TG_OP = 'UPDATE' THEN
                SELECT array_agg(version_id), array_agg(dim_cost_center), array_agg(dim_account) INTO v, c, a FROM (SELECT DISTINCT version_id, dim_cost_center, dim_account FROM (SELECT * FROM new_rows UNION ALL SELECT * FROM old_rows) u WHERE version_id NOT IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind = 'actuals')) x;
            ELSE
                SELECT array_agg(version_id), array_agg(dim_cost_center), array_agg(dim_account) INTO v, c, a FROM (SELECT DISTINCT version_id, dim_cost_center, dim_account FROM old_rows WHERE version_id NOT IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind = 'actuals')) x;
            END IF;
            IF v IS NULL THEN
                RETURN NULL;
            END IF;

            PERFORM 1 FROM "__SCHEMA__".planning_round WHERE version_id = ANY (v) FOR SHARE;
            INSERT INTO "__SCHEMA__".workflow_status (version_id, cost_center_id, status)
                SELECT DISTINCT t.version_id, t.dim_cost_center, 'not_started' FROM unnest(v, c, a) AS t(version_id, dim_cost_center, dim_account)
                WHERE t.version_id IN (SELECT version_id FROM "__SCHEMA__".planning_round)
                ON CONFLICT DO NOTHING;
            PERFORM 1 FROM "__SCHEMA__".workflow_status w
                WHERE (w.version_id, w.cost_center_id) IN
                      (SELECT t.version_id, t.dim_cost_center FROM unnest(v, c, a) AS t(version_id, dim_cost_center, dim_account))
                FOR UPDATE;

            SELECT CASE
                       WHEN r.version_id IS NULL THEN 'the version has no planning round'
                       WHEN NOT (r.status = 'open' OR "__SCHEMA__".prepares(r.status)) THEN 'the planning round is ' || r.status
                       WHEN NOT coalesce(m.enabled, false)
                           THEN 'the cost center is not enabled in the round'
                       WHEN fa.source = 'forwarding'
                           THEN 'the account is written by cost forwarding'
                       WHEN fa.source = 'personnel'
                           THEN 'the account is written by personnel costs'
                       WHEN w.status NOT IN ('not_started', 'in_progress')
                           THEN 'the cost center is ' || w.status
                       WHEN h.account_id IS NOT NULL
                           THEN 'the account is not planned in the round'
                       ELSE 'the account is not in the cost center''s account set'
                   END
            INTO problem
            FROM unnest(v, c, a) AS t(version_id, dim_cost_center, dim_account)
            LEFT JOIN "__SCHEMA__".planning_round r ON r.version_id = t.version_id
            LEFT JOIN "__SCHEMA__".round_member m
                ON m.version_id = t.version_id AND m.module = 'cost_center'
                AND m.member_id = t.dim_cost_center
            LEFT JOIN "__SCHEMA__".workflow_status w
                ON w.version_id = t.version_id AND w.cost_center_id = t.dim_cost_center
            LEFT JOIN "__SCHEMA__".not_planned_account h
                ON h.version_id = t.version_id AND h.module = 'cost_center'
                AND h.account_id = t.dim_account
            
        LEFT JOIN LATERAL (
            SELECT w.account_id, w.source FROM "__SCHEMA__".written_account w
            WHERE w.version_id = t.version_id AND w.account_id = t.dim_account LIMIT 1
        ) fa ON true
            
        LEFT JOIN LATERAL (
            SELECT x.set_id FROM "__SCHEMA__".closure cl
            JOIN "__SCHEMA__".account_set_assignment x
                ON x.member_id = cl.ancestor_id AND x.version_id = t.version_id
                AND x.module = 'cost_center'
            WHERE cl.hier_id = 1 AND cl.descendant_id = t.dim_cost_center
            ORDER BY cl.depth LIMIT 1
        ) cs ON true
            WHERE r.version_id IS NULL OR NOT (r.status = 'open' OR "__SCHEMA__".prepares(r.status)) OR NOT coalesce(m.enabled, false)
               OR (fa.account_id IS NOT NULL AND NOT controller)
               OR (fa.account_id IS NULL AND (w.status NOT IN ('not_started', 'in_progress') OR h.account_id IS NOT NULL OR (TG_OP <> 'DELETE' AND cs.set_id IS NOT NULL
               AND t.dim_account NOT IN (SELECT account_id FROM "__SCHEMA__".credit_account)
               AND NOT EXISTS (
                   SELECT 1 FROM "__SCHEMA__".account_set_account i
                   JOIN "__SCHEMA__".closure ca ON ca.hier_id = 2 AND ca.ancestor_id = i.account_id
                   WHERE i.set_id = cs.set_id AND ca.descendant_id = t.dim_account))))
            LIMIT 1;
            IF problem IS NOT NULL THEN
                RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
            END IF;

            IF TG_OP <> 'DELETE' THEN
                WITH started AS (
                    UPDATE "__SCHEMA__".workflow_status w
                    SET status = 'in_progress', changed_at = now(),
                        changed_by = nullif(current_setting('app.actor', true), '')
                    WHERE w.status = 'not_started'
                      AND (w.version_id, w.cost_center_id) IN
                          (SELECT t.version_id, t.dim_cost_center FROM unnest(v, c, a) AS t(version_id, dim_cost_center, dim_account) 
        LEFT JOIN LATERAL (
            SELECT w.account_id, w.source FROM "__SCHEMA__".written_account w
            WHERE w.version_id = t.version_id AND w.account_id = t.dim_account LIMIT 1
        ) fa ON true
                           WHERE fa.account_id IS NULL)
                      AND w.version_id IN
                          (SELECT version_id FROM "__SCHEMA__".planning_round WHERE status = 'open')
                    RETURNING w.version_id, w.cost_center_id
                )
                INSERT INTO "__SCHEMA__".workflow_event
                    (version_id, cost_center_id, actor, from_status, to_status)
                SELECT version_id, cost_center_id,
                       nullif(current_setting('app.actor', true), ''),
                       'not_started', 'in_progress'
                FROM started;
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.lock_fact_workforce() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            v smallint[];
            c int[];
            problem text;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                SELECT array_agg(version_id), array_agg(dim_cost_center) INTO v, c FROM (SELECT DISTINCT version_id, dim_cost_center FROM new_rows WHERE version_id NOT IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind = 'actuals')) x;
            ELSIF TG_OP = 'UPDATE' THEN
                SELECT array_agg(version_id), array_agg(dim_cost_center) INTO v, c FROM (SELECT DISTINCT version_id, dim_cost_center FROM (SELECT * FROM new_rows UNION ALL SELECT * FROM old_rows) u WHERE version_id NOT IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind = 'actuals')) x;
            ELSE
                SELECT array_agg(version_id), array_agg(dim_cost_center) INTO v, c FROM (SELECT DISTINCT version_id, dim_cost_center FROM old_rows WHERE version_id NOT IN (SELECT version_id FROM "__SCHEMA__".version WHERE kind = 'actuals')) x;
            END IF;
            IF v IS NULL THEN
                RETURN NULL;
            END IF;
            PERFORM 1 FROM "__SCHEMA__".planning_round WHERE version_id = ANY (v) FOR SHARE;
            INSERT INTO "__SCHEMA__".workflow_status (version_id, cost_center_id, status)
                SELECT DISTINCT t.version_id, t.cost_center_id, 'not_started' FROM unnest(v, c) AS t(version_id, cost_center_id)
                WHERE t.version_id IN (SELECT version_id FROM "__SCHEMA__".planning_round)
                ON CONFLICT DO NOTHING;
            PERFORM 1 FROM "__SCHEMA__".workflow_status w
                WHERE (w.version_id, w.cost_center_id) IN
                      (SELECT t.version_id, t.cost_center_id FROM unnest(v, c) AS t(version_id, cost_center_id))
                FOR UPDATE;
            
        SELECT CASE
                   WHEN r.version_id IS NULL THEN 'the version has no planning round'
                   WHEN NOT (r.status = 'open' OR "__SCHEMA__".prepares(r.status)) THEN 'the planning round is ' || r.status
                   WHEN rm.version_id IS NULL THEN 'the round has no workforce planning'
                   WHEN NOT coalesce(m.enabled, false)
                       THEN 'the cost center is not enabled in the round'
                   WHEN coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress')
                       THEN 'the cost center is ' || w.status
               END
        INTO problem
        FROM unnest(v, c) AS t(version_id, cost_center_id)
        LEFT JOIN "__SCHEMA__".planning_round r ON r.version_id = t.version_id
        LEFT JOIN "__SCHEMA__".round_module rm
            ON rm.version_id = t.version_id AND rm.module = 'workforce'
        LEFT JOIN "__SCHEMA__".round_member m
            ON m.version_id = t.version_id AND m.module = 'cost_center'
            AND m.member_id = t.cost_center_id
        LEFT JOIN "__SCHEMA__".workflow_status w
            ON w.version_id = t.version_id AND w.cost_center_id = t.cost_center_id
        WHERE r.version_id IS NULL OR NOT (r.status = 'open' OR "__SCHEMA__".prepares(r.status)) OR rm.version_id IS NULL
           OR NOT coalesce(m.enabled, false)
           OR coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress')
        LIMIT 1;
        IF problem IS NOT NULL THEN
            RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
        END IF;
    
            IF TG_OP <> 'DELETE' THEN
                WITH started AS (
                    UPDATE "__SCHEMA__".workflow_status w
                    SET status = 'in_progress', changed_at = now(),
                        changed_by = nullif(current_setting('app.actor', true), '')
                    WHERE w.status = 'not_started'
                      AND (w.version_id, w.cost_center_id) IN
                          (SELECT t.version_id, t.cost_center_id FROM unnest(v, c) AS t(version_id, cost_center_id))
                      AND w.version_id IN
                          (SELECT version_id FROM "__SCHEMA__".planning_round WHERE status = 'open')
                    RETURNING w.version_id, w.cost_center_id
                )
                INSERT INTO "__SCHEMA__".workflow_event
                    (version_id, cost_center_id, actor, from_status, to_status)
                SELECT version_id, cost_center_id,
                       nullif(current_setting('app.actor', true), ''),
                       'not_started', 'in_progress'
                FROM started;
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.lock_line_spread() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            t "__SCHEMA__".line_spread;
            problem text;
        BEGIN
            t := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            SELECT CASE
                       WHEN NOT (r.status = 'open' OR "__SCHEMA__".prepares(r.status)) THEN 'the planning round is ' || r.status
                       WHEN NOT coalesce(m.enabled, false)
                           THEN 'the cost center is not enabled in the round'
                       WHEN coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress')
                           THEN 'the cost center is ' || w.status
                   END
            INTO problem
            FROM "__SCHEMA__".planning_round r
            LEFT JOIN "__SCHEMA__".round_member m
                ON m.version_id = r.version_id AND m.module = t.module
                AND m.member_id = t.cost_center_id
            LEFT JOIN "__SCHEMA__".workflow_status w
                ON w.version_id = r.version_id AND w.cost_center_id = t.cost_center_id
            WHERE r.version_id = t.version_id
            FOR SHARE OF r;
            IF problem IS NOT NULL THEN
                RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.lock_position_pay() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            k "__SCHEMA__".position_pay;
            cc int;
            problem text;
            was_hr bytea;
            was_charged int;
            was_excluded int[] := '{}';
            system boolean := EXISTS (SELECT 1 FROM "__SCHEMA__".app_user u
                                      WHERE u.user_id = nullif(current_setting('app.user_id', true), '')::int AND u.is_system);
        BEGIN
            IF TG_OP = 'DELETE' THEN k := OLD; ELSE k := NEW; END IF;
            SELECT p.cost_center_id INTO cc FROM "__SCHEMA__".workforce_position p
                WHERE p.position_id = k.position_id;
            IF cc IS NULL THEN  -- deleted with its position
                RETURN NULL;
            END IF;
            PERFORM 1 FROM "__SCHEMA__".planning_round WHERE version_id = k.version_id FOR SHARE;
            SELECT CASE
                       WHEN NOT (r.status = 'open' OR r.status = 'setup' AND ("__SCHEMA__".writes_as_controller() OR "__SCHEMA__".writes_as_hr())) THEN 'the planning round is ' || r.status
                       WHEN NOT coalesce(m.enabled, false)
                           THEN 'the cost center is not enabled in the round'
                       ELSE 'the cost center is ' || w.status
                   END
            INTO problem
            FROM "__SCHEMA__".planning_round r
            LEFT JOIN "__SCHEMA__".round_member m
                ON m.version_id = r.version_id AND m.module = 'cost_center' AND m.member_id = cc
            LEFT JOIN "__SCHEMA__".workflow_status w
                ON w.version_id = r.version_id AND w.cost_center_id = cc
            WHERE r.version_id = k.version_id
              AND (NOT (r.status = 'open' OR r.status = 'setup' AND ("__SCHEMA__".writes_as_controller() OR "__SCHEMA__".writes_as_hr())) OR NOT coalesce(m.enabled, false)
                   OR coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress'));
            IF problem IS NOT NULL THEN
                RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
            END IF;
            IF TG_OP = 'DELETE' OR system THEN
                RETURN NULL;
            END IF;
            IF NOT "__SCHEMA__".writes_as_hr() AND NOT EXISTS (
                    SELECT 1 FROM "__SCHEMA__".pay_handover h
                    WHERE h.version_id = k.version_id AND h.cost_center_id = cc) THEN
                RAISE EXCEPTION 'plan values are locked: HR hasn''t handed this cost center''s '
                    'pay over yet' USING ERRCODE = 'PL001';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                was_hr := OLD.hr_salary;
                was_charged := OLD.charged_to_id;
            END IF;
            -- HR's salary comes with the position, taken by the system user's rights; HR corrects it.
            IF NEW.hr_salary IS DISTINCT FROM was_hr AND NOT "__SCHEMA__".writes_as_hr() THEN
                RAISE EXCEPTION 'plan values are locked: only HR changes HR''s salary'
                    USING ERRCODE = 'PL001';
            END IF;
            IF NEW.charged_to_id IS DISTINCT FROM was_charged AND NOT "__SCHEMA__".writes_as_hr() THEN
                RAISE EXCEPTION 'plan values are locked: only HR charges a position to another '
                    'cost center' USING ERRCODE = 'PL001';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                was_excluded := OLD.excluded_rules;
            END IF;
            IF NEW.excluded_rules IS DISTINCT FROM was_excluded AND NOT "__SCHEMA__".writes_as_hr() THEN
                RAISE EXCEPTION 'plan values are locked: only HR excludes a position from a '
                    'rule' USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.lock_workforce_row() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            l record;
            problem text;
        BEGIN
            IF TG_OP = 'DELETE' THEN l := OLD; ELSE l := NEW; END IF;
            PERFORM 1 FROM "__SCHEMA__".planning_round WHERE version_id = l.version_id FOR SHARE;
            
        SELECT CASE
                   WHEN r.version_id IS NULL THEN 'the version has no planning round'
                   WHEN NOT (r.status = 'open' OR "__SCHEMA__".prepares(r.status)) THEN 'the planning round is ' || r.status
                   WHEN rm.version_id IS NULL THEN 'the round has no workforce planning'
                   WHEN NOT coalesce(m.enabled, false)
                       THEN 'the cost center is not enabled in the round'
                   WHEN coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress')
                       THEN 'the cost center is ' || w.status
               END
        INTO problem
        FROM (SELECT l.version_id, l.cost_center_id) AS t
        LEFT JOIN "__SCHEMA__".planning_round r ON r.version_id = t.version_id
        LEFT JOIN "__SCHEMA__".round_module rm
            ON rm.version_id = t.version_id AND rm.module = 'workforce'
        LEFT JOIN "__SCHEMA__".round_member m
            ON m.version_id = t.version_id AND m.module = 'cost_center'
            AND m.member_id = t.cost_center_id
        LEFT JOIN "__SCHEMA__".workflow_status w
            ON w.version_id = t.version_id AND w.cost_center_id = t.cost_center_id
        WHERE r.version_id IS NULL OR NOT (r.status = 'open' OR "__SCHEMA__".prepares(r.status)) OR rm.version_id IS NULL
           OR NOT coalesce(m.enabled, false)
           OR coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress')
        LIMIT 1;
        IF problem IS NOT NULL THEN
            RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
        END IF;
    
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.log_annotation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            INSERT INTO "__SCHEMA__".annotation_log (actor, batch_id, table_name, op, old, new)
            VALUES (nullif(current_setting('app.actor', true), ''),
                    nullif(current_setting('app.batch_id', true), ''),
                    TG_TABLE_NAME, TG_OP,
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END);
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.log_capacity_line() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            o "__SCHEMA__".capacity_line;  -- stays all NULL for an insert
            n "__SCHEMA__".capacity_line;  -- stays all NULL for a delete
            k "__SCHEMA__".capacity_line;
        BEGIN
            IF TG_OP <> 'INSERT' THEN o := OLD; k := OLD; END IF;
            IF TG_OP <> 'DELETE' THEN n := NEW; k := NEW; END IF;
            IF TG_OP = 'UPDATE' AND (o.rate, o.variable_rate, o.active, o.basis, o.profile_id) IS NOT DISTINCT FROM (n.rate, n.variable_rate, n.active, n.basis, n.profile_id) THEN
                RETURN NULL;
            END IF;
            INSERT INTO "__SCHEMA__".capacity_line_log (actor, batch_id, op, version_id, cost_center_id, work_center_id, activity_type_id, old_rate, old_variable_rate, old_active, old_basis, old_profile_id, new_rate, new_variable_rate, new_active, new_basis, new_profile_id) VALUES (
                nullif(current_setting('app.actor', true), ''),
                nullif(current_setting('app.batch_id', true), ''),
                TG_OP, k.version_id, k.cost_center_id, k.work_center_id, k.activity_type_id,
                o.rate, o.variable_rate, o.active, o.basis, o.profile_id, n.rate, n.variable_rate, n.active, n.basis, n.profile_id);
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.log_capacity_staff() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            l record;
        BEGIN
            IF TG_OP = 'DELETE' THEN l := OLD; ELSE l := NEW; END IF;
            IF TG_OP = 'UPDATE' AND to_jsonb(OLD) - 'changed_at' - 'changed_by'
                                  = to_jsonb(NEW) - 'changed_at' - 'changed_by' THEN
                RETURN NULL;
            END IF;
            INSERT INTO "__SCHEMA__".capacity_staff_log
                (actor, batch_id, op, target, version_id, cost_center_id, old, new)
            VALUES (nullif(current_setting('app.actor', true), ''),
                    nullif(current_setting('app.batch_id', true), ''),
                    TG_OP, TG_TABLE_NAME, l.version_id, l.cost_center_id,
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END);
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.log_capex() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            l record;
            v smallint;
        BEGIN
            IF TG_OP = 'DELETE' THEN l := OLD; ELSE l := NEW; END IF;
            IF TG_OP = 'UPDATE' AND to_jsonb(OLD) - 'changed_at' - 'changed_by' - 'row_version'
                                  = to_jsonb(NEW) - 'changed_at' - 'changed_by' - 'row_version'
            THEN
                RETURN NULL;
            END IF;
            IF TG_TABLE_NAME = 'capex_item' THEN
                v := l.version_id;
            ELSE
                SELECT version_id INTO v FROM "__SCHEMA__".capex_item WHERE item_id = l.item_id;
            END IF;
            INSERT INTO "__SCHEMA__".capex_log (actor, batch_id, op, target, version_id, item_id, old, new)
            VALUES (nullif(current_setting('app.actor', true), ''),
                    nullif(current_setting('app.batch_id', true), ''),
                    TG_OP, TG_TABLE_NAME, v, l.item_id,
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END);
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.log_hr_role() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF NEW.is_hr IS DISTINCT FROM coalesce(OLD.is_hr, false) THEN
                INSERT INTO "__SCHEMA__".pay_access_log (actor, action, user_id)
                VALUES (nullif(current_setting('app.actor', true), ''), CASE WHEN NEW.is_hr THEN 'hr_granted' ELSE 'hr_revoked' END,
                        NEW.user_id);
            END IF;
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.log_pay_handover() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            k "__SCHEMA__".pay_handover;
        BEGIN
            IF TG_OP = 'DELETE' THEN k := OLD; ELSE k := NEW; END IF;
            INSERT INTO "__SCHEMA__".pay_access_log (actor, action, version_id, cost_center_id)
            VALUES (nullif(current_setting('app.actor', true), ''), CASE WHEN TG_OP = 'DELETE' THEN 'handover_withdrawn'
                                  ELSE 'handed_over' END,
                    k.version_id, k.cost_center_id);
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.log_pay_reader() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            k "__SCHEMA__".pay_reader;
        BEGIN
            IF TG_OP = 'DELETE' THEN k := OLD; ELSE k := NEW; END IF;
            INSERT INTO "__SCHEMA__".pay_access_log (actor, action, version_id, cost_center_id, user_id)
            VALUES (nullif(current_setting('app.actor', true), ''), CASE WHEN TG_OP = 'DELETE' THEN 'reader_removed'
                                  ELSE 'reader_added' END,
                    k.version_id, k.cost_center_id, k.user_id);
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.log_pay_release() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            INSERT INTO "__SCHEMA__".pay_access_log (actor, action, version_id)
            VALUES (nullif(current_setting('app.actor', true), ''), CASE WHEN TG_OP = 'DELETE' THEN 'release_withdrawn'
                                  ELSE 'released' END,
                    CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END);
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.log_position_pay() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            k "__SCHEMA__".position_pay;
            cc int;
        BEGIN
            IF TG_OP = 'DELETE' THEN k := OLD; ELSE k := NEW; END IF;
            IF TG_OP = 'UPDATE' AND to_jsonb(OLD) - 'changed_at' - 'changed_by'
                                  = to_jsonb(NEW) - 'changed_at' - 'changed_by' THEN
                RETURN NULL;
            END IF;
            SELECT p.cost_center_id INTO cc FROM "__SCHEMA__".workforce_position p
                WHERE p.position_id = k.position_id;
            IF cc IS NULL THEN  -- deleted with its position, whose log has the row
                RETURN NULL;
            END IF;
            INSERT INTO "__SCHEMA__".position_pay_log
                (actor, batch_id, op, version_id, cost_center_id, position_id, old, new)
            VALUES (nullif(current_setting('app.actor', true), ''),
                    nullif(current_setting('app.batch_id', true), ''),
                    TG_OP, k.version_id, cc, k.position_id,
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END);
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.log_workforce_position() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            k "__SCHEMA__".workforce_position;
        BEGIN
            IF TG_OP = 'DELETE' THEN k := OLD; ELSE k := NEW; END IF;
            IF TG_OP = 'UPDATE' AND to_jsonb(OLD) - 'changed_at' - 'changed_by'
                                  = to_jsonb(NEW) - 'changed_at' - 'changed_by' THEN
                RETURN NULL;
            END IF;
            INSERT INTO "__SCHEMA__".workforce_position_log
                (actor, batch_id, op, version_id, cost_center_id, position_id, old, new)
            VALUES (nullif(current_setting('app.actor', true), ''),
                    nullif(current_setting('app.batch_id', true), ''),
                    TG_OP, k.version_id, k.cost_center_id, k.position_id,
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END);
            RETURN NULL;
        END $$;

CREATE FUNCTION __SCHEMA__.prepares(status text) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
            SELECT status = 'setup' AND EXISTS (
                SELECT 1 FROM "__SCHEMA__".app_user u
                WHERE u.user_id = nullif(current_setting('app.user_id', true), '')::int
                  AND u.is_controller AND NOT u.disabled)
        $$;

CREATE FUNCTION __SCHEMA__.writes_as_controller() RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
            SELECT EXISTS (
                SELECT 1 FROM "__SCHEMA__".app_user u
                WHERE u.user_id = nullif(current_setting('app.user_id', true), '')::int
                  AND u.is_controller AND NOT u.disabled)
        $$;

CREATE FUNCTION __SCHEMA__.writes_as_hr() RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
            SELECT EXISTS (
                SELECT 1 FROM "__SCHEMA__".app_user u
                WHERE u.user_id = nullif(current_setting('app.user_id', true), '')::int AND u.is_hr AND NOT u.disabled)
        $$;

CREATE TABLE __SCHEMA__.account_set (
    set_id integer NOT NULL,
    version_id smallint NOT NULL,
    module text NOT NULL,
    name text NOT NULL
);

CREATE TABLE __SCHEMA__.account_set_account (
    set_id integer NOT NULL,
    account_id integer NOT NULL
);

CREATE TABLE __SCHEMA__.account_set_assignment (
    version_id smallint NOT NULL,
    module text NOT NULL,
    member_id integer NOT NULL,
    set_id integer NOT NULL
);

ALTER TABLE __SCHEMA__.account_set ALTER COLUMN set_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.account_set_set_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.annotation_log (
    id bigint NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    actor text,
    batch_id text,
    table_name text NOT NULL,
    op text NOT NULL,
    old jsonb,
    new jsonb
);

ALTER TABLE __SCHEMA__.annotation_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.annotation_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.app_user (
    user_id integer NOT NULL,
    login text NOT NULL,
    email text,
    display_name text NOT NULL,
    is_admin boolean DEFAULT false NOT NULL,
    is_controller boolean DEFAULT false NOT NULL,
    is_viewer boolean DEFAULT false NOT NULL,
    is_system boolean DEFAULT false NOT NULL,
    disabled boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    is_hr boolean DEFAULT false NOT NULL,
    number_separators text,
    number_decimals boolean,
    number_scale text,
    number_units boolean,
    password_hash text,
    identity text,
    CONSTRAINT ck_app_user_login_lowercase CHECK ((login = lower(login))),
    CONSTRAINT ck_app_user_number_scale CHECK ((number_scale = ANY (ARRAY['full'::text, 'k'::text, 'M'::text]))),
    CONSTRAINT ck_app_user_number_separators CHECK ((number_separators = ANY (ARRAY['comma_dot'::text, 'dot_comma'::text, 'apostrophe_dot'::text, 'space_comma'::text])))
);

ALTER TABLE __SCHEMA__.app_user ALTER COLUMN user_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.app_user_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.approver_assignment (
    member_id integer NOT NULL,
    user_id integer NOT NULL
);

CREATE TABLE __SCHEMA__.capacity_grant (
    member_id integer NOT NULL,
    user_id integer NOT NULL,
    granted_by text,
    granted_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE __SCHEMA__.closure (
    hier_id smallint NOT NULL,
    ancestor_id integer NOT NULL,
    descendant_id integer NOT NULL,
    depth smallint NOT NULL
);

CREATE TABLE __SCHEMA__.member (
    member_id integer NOT NULL,
    dim_id smallint NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    is_group boolean NOT NULL,
    entity_code text,
    owner text,
    account_type text,
    valid_from date,
    valid_to date,
    origin text DEFAULT 'source'::text NOT NULL,
    source_name text,
    cost_center_code text,
    account_code text,
    variable_account_code text,
    missing_since timestamp with time zone,
    external boolean,
    CONSTRAINT ck_member_origin CHECK ((origin = ANY (ARRAY['source'::text, 'manual'::text])))
);

CREATE TABLE __SCHEMA__.owner_override (
    cost_center_id integer NOT NULL,
    user_id integer,
    set_by text,
    set_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE VIEW __SCHEMA__.cost_center_owner AS
 SELECT m.member_id AS cost_center_id,
        CASE
            WHEN (o.cost_center_id IS NOT NULL) THEN o.user_id
            ELSE d.user_id
        END AS user_id,
    d.user_id AS matched_user_id,
    (o.cost_center_id IS NOT NULL) AS overridden
   FROM ((__SCHEMA__.member m
     LEFT JOIN __SCHEMA__.owner_override o ON ((o.cost_center_id = m.member_id)))
     LEFT JOIN LATERAL ( SELECT u.user_id
           FROM __SCHEMA__.app_user u
          WHERE ((NOT u.disabled) AND (NOT u.is_system) AND (m.owner IS NOT NULL) AND ((lower(u.email) = lower(m.owner)) OR (u.login = lower(m.owner))))
          ORDER BY u.user_id
         LIMIT 1) d ON (true))
  WHERE ((m.dim_id = 2) AND (NOT m.is_group));

CREATE TABLE __SCHEMA__.planner_grant (
    member_id integer NOT NULL,
    user_id integer NOT NULL,
    granted_by text,
    granted_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE VIEW __SCHEMA__.cost_center_access AS
 SELECT u.user_id,
    m.member_id AS cost_center_id,
    u.is_controller AS can_write
   FROM (__SCHEMA__.app_user u
     CROSS JOIN __SCHEMA__.member m)
  WHERE ((u.is_controller OR u.is_viewer) AND (NOT u.disabled) AND (m.dim_id = 2) AND (NOT m.is_group))
UNION ALL
 SELECT o.user_id,
    o.cost_center_id,
    true AS can_write
   FROM (__SCHEMA__.cost_center_owner o
     JOIN __SCHEMA__.app_user u USING (user_id))
  WHERE (NOT u.disabled)
UNION ALL
 SELECT g.user_id,
    c.descendant_id AS cost_center_id,
    true AS can_write
   FROM ((__SCHEMA__.planner_grant g
     JOIN __SCHEMA__.app_user u USING (user_id))
     JOIN __SCHEMA__.closure c ON (((c.hier_id = 1) AND (c.ancestor_id = g.member_id))))
  WHERE (NOT u.disabled)
UNION ALL
 SELECT a.user_id,
    c.descendant_id AS cost_center_id,
    false AS can_write
   FROM ((__SCHEMA__.approver_assignment a
     JOIN __SCHEMA__.app_user u USING (user_id))
     JOIN __SCHEMA__.closure c ON (((c.hier_id = 1) AND (c.ancestor_id = a.member_id))))
  WHERE (NOT u.disabled)
UNION ALL
 SELECT g.user_id,
    c.descendant_id AS cost_center_id,
    false AS can_write
   FROM ((__SCHEMA__.capacity_grant g
     JOIN __SCHEMA__.app_user u USING (user_id))
     JOIN __SCHEMA__.closure c ON (((c.hier_id = 1) AND (c.ancestor_id = g.member_id))))
  WHERE (NOT u.disabled);

CREATE VIEW __SCHEMA__.capacity_access AS
 SELECT cost_center_access.user_id,
    cost_center_access.cost_center_id,
    cost_center_access.can_write
   FROM __SCHEMA__.cost_center_access
UNION ALL
 SELECT g.user_id,
    c.descendant_id AS cost_center_id,
    true AS can_write
   FROM ((__SCHEMA__.capacity_grant g
     JOIN __SCHEMA__.app_user u USING (user_id))
     JOIN __SCHEMA__.closure c ON (((c.hier_id = 1) AND (c.ancestor_id = g.member_id))))
  WHERE (NOT u.disabled);

CREATE TABLE __SCHEMA__.capacity_change_log (
    id bigint NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    actor text,
    batch_id text,
    op text NOT NULL,
    version_id smallint NOT NULL,
    period date NOT NULL,
    dim_cost_center integer NOT NULL,
    dim_work_center integer NOT NULL,
    dim_activity_type integer NOT NULL,
    old_amount numeric(18,2),
    new_amount numeric(18,2)
);

ALTER TABLE __SCHEMA__.capacity_change_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.capacity_change_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.capacity_line (
    version_id smallint NOT NULL,
    module text DEFAULT 'capacity'::text NOT NULL,
    cost_center_id integer NOT NULL,
    work_center_id integer NOT NULL,
    activity_type_id integer NOT NULL,
    rate numeric(18,4) NOT NULL,
    variable_rate numeric(18,4),
    active boolean DEFAULT true NOT NULL,
    basis text,
    profile_id smallint,
    changed_by text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    staffed boolean DEFAULT false NOT NULL,
    CONSTRAINT ck_capacity_line_basis CHECK ((basis = ANY (ARRAY['even'::text, 'reference'::text, 'profile'::text]))),
    CONSTRAINT ck_capacity_line_module CHECK ((module = 'capacity'::text)),
    CONSTRAINT ck_capacity_line_profile CHECK (((basis = 'profile'::text) = (profile_id IS NOT NULL))),
    CONSTRAINT ck_capacity_line_rate CHECK (((rate >= (0)::numeric) AND (COALESCE(variable_rate, (0)::numeric) >= (0)::numeric)))
);

ALTER TABLE ONLY __SCHEMA__.capacity_line FORCE ROW LEVEL SECURITY;

CREATE TABLE __SCHEMA__.capacity_line_log (
    id bigint NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    actor text,
    batch_id text,
    op text NOT NULL,
    version_id smallint NOT NULL,
    cost_center_id integer NOT NULL,
    work_center_id integer NOT NULL,
    activity_type_id integer NOT NULL,
    old_rate numeric(18,4),
    old_variable_rate numeric(18,4),
    old_active boolean,
    old_basis text,
    old_profile_id smallint,
    new_rate numeric(18,4),
    new_variable_rate numeric(18,4),
    new_active boolean,
    new_basis text,
    new_profile_id smallint
);

ALTER TABLE __SCHEMA__.capacity_line_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.capacity_line_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.capacity_position (
    position_id integer NOT NULL,
    version_id smallint NOT NULL,
    module text DEFAULT 'capacity'::text NOT NULL,
    cost_center_id integer NOT NULL,
    productive_percent numeric(5,2),
    hours numeric(10,2),
    work_center_id integer,
    activity_type_id integer,
    changed_by text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_capacity_position_hours CHECK ((hours >= (0)::numeric)),
    CONSTRAINT ck_capacity_position_module CHECK ((module = 'capacity'::text)),
    CONSTRAINT ck_capacity_position_productive_percent CHECK (((productive_percent >= (0)::numeric) AND (productive_percent <= (100)::numeric)))
);

ALTER TABLE ONLY __SCHEMA__.capacity_position FORCE ROW LEVEL SECURITY;

CREATE TABLE __SCHEMA__.capacity_staff_log (
    id bigint NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    actor text,
    batch_id text,
    op text NOT NULL,
    target text NOT NULL,
    version_id smallint NOT NULL,
    cost_center_id integer NOT NULL,
    old jsonb,
    new jsonb
);

ALTER TABLE __SCHEMA__.capacity_staff_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.capacity_staff_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.capacity_unit (
    version_id smallint NOT NULL,
    module text DEFAULT 'capacity'::text NOT NULL,
    cost_center_id integer NOT NULL,
    typed_hours boolean DEFAULT true NOT NULL,
    staff_hours boolean DEFAULT false NOT NULL,
    default_work_center_id integer,
    default_activity_type_id integer,
    default_percent numeric(5,2),
    changed_by text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_capacity_unit_default_percent CHECK (((default_percent >= (0)::numeric) AND (default_percent <= (100)::numeric))),
    CONSTRAINT ck_capacity_unit_hours CHECK ((typed_hours OR staff_hours)),
    CONSTRAINT ck_capacity_unit_module CHECK ((module = 'capacity'::text))
);

ALTER TABLE ONLY __SCHEMA__.capacity_unit FORCE ROW LEVEL SECURITY;

CREATE TABLE __SCHEMA__.capex_cash_out (
    item_id integer NOT NULL,
    period date NOT NULL,
    amount numeric(18,2) NOT NULL,
    CONSTRAINT ck_capex_cash_out_amount CHECK ((amount > (0)::numeric)),
    CONSTRAINT ck_capex_cash_out_period CHECK ((EXTRACT(day FROM period) = (1)::numeric))
);

ALTER TABLE ONLY __SCHEMA__.capex_cash_out FORCE ROW LEVEL SECURITY;

CREATE VIEW __SCHEMA__.capex_confirmer AS
 SELECT u.user_id,
    m.member_id AS cost_center_id
   FROM (__SCHEMA__.app_user u
     CROSS JOIN __SCHEMA__.member m)
  WHERE (u.is_controller AND (NOT u.disabled) AND (m.dim_id = 2) AND (NOT m.is_group))
UNION
 SELECT a.user_id,
    c.descendant_id AS cost_center_id
   FROM ((__SCHEMA__.approver_assignment a
     JOIN __SCHEMA__.app_user u USING (user_id))
     JOIN __SCHEMA__.closure c ON (((c.hier_id = 1) AND (c.ancestor_id = a.member_id))))
  WHERE ((NOT u.disabled) AND (NOT (EXISTS ( SELECT 1
           FROM __SCHEMA__.cost_center_owner o
          WHERE ((o.user_id = a.user_id) AND (o.cost_center_id = c.descendant_id))))) AND (NOT (EXISTS ( SELECT 1
           FROM (__SCHEMA__.planner_grant g
             JOIN __SCHEMA__.closure p ON (((p.hier_id = 1) AND (p.ancestor_id = g.member_id))))
          WHERE ((g.user_id = a.user_id) AND (p.descendant_id = c.descendant_id))))));

CREATE TABLE __SCHEMA__.capex_item (
    item_id integer NOT NULL,
    version_id smallint NOT NULL,
    module text DEFAULT 'capex'::text NOT NULL,
    kind text NOT NULL,
    project_id integer,
    cost_center_id integer NOT NULL,
    name text NOT NULL,
    type_id smallint,
    priority smallint,
    start_month date,
    amount numeric(18,2),
    status text,
    status_by text,
    status_at timestamp with time zone,
    row_version integer DEFAULT 1 NOT NULL,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    changed_by text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_capex_item_amount CHECK ((amount >= (0)::numeric)),
    CONSTRAINT ck_capex_item_in_project CHECK ((((project_id IS NULL) = (priority IS NOT NULL)) AND ((project_id IS NULL) = (status IS NOT NULL)))),
    CONSTRAINT ck_capex_item_kind CHECK ((kind = ANY (ARRAY['line'::text, 'project'::text]))),
    CONSTRAINT ck_capex_item_module CHECK ((module = 'capex'::text)),
    CONSTRAINT ck_capex_item_name CHECK ((btrim(name) <> ''::text)),
    CONSTRAINT ck_capex_item_priority CHECK (((priority >= 1) AND (priority <= 3))),
    CONSTRAINT ck_capex_item_project CHECK (((kind = 'line'::text) OR ((project_id IS NULL) AND (type_id IS NULL) AND (start_month IS NULL) AND (amount IS NULL)))),
    CONSTRAINT ck_capex_item_start_month CHECK ((EXTRACT(day FROM start_month) = (1)::numeric)),
    CONSTRAINT ck_capex_item_status CHECK ((status = ANY (ARRAY['open'::text, 'confirmed'::text, 'declined'::text])))
);

ALTER TABLE ONLY __SCHEMA__.capex_item FORCE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.capex_item ALTER COLUMN item_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.capex_item_item_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.capex_log (
    id bigint NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    actor text,
    batch_id text,
    op text NOT NULL,
    target text NOT NULL,
    version_id smallint,
    item_id integer NOT NULL,
    old jsonb,
    new jsonb
);

ALTER TABLE __SCHEMA__.capex_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.capex_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.capex_setting (
    version_id smallint NOT NULL,
    module text DEFAULT 'capex'::text NOT NULL,
    project_threshold numeric(18,2),
    cash_out boolean DEFAULT false NOT NULL,
    changed_by text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_capex_setting_module CHECK ((module = 'capex'::text)),
    CONSTRAINT ck_capex_setting_project_threshold CHECK ((project_threshold > (0)::numeric))
);

CREATE TABLE __SCHEMA__.capex_type (
    type_id smallint NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    asset_account text,
    useful_life smallint,
    active boolean DEFAULT true NOT NULL,
    CONSTRAINT ck_capex_type_useful_life CHECK (((useful_life >= 1) AND (useful_life <= 100)))
);

ALTER TABLE __SCHEMA__.capex_type ALTER COLUMN type_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.capex_type_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.change_log (
    id bigint NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    actor text,
    batch_id text,
    op text NOT NULL,
    version_id smallint NOT NULL,
    period date NOT NULL,
    dim_cost_center integer NOT NULL,
    dim_account integer NOT NULL,
    old_amount numeric(18,2),
    new_amount numeric(18,2)
);

ALTER TABLE __SCHEMA__.change_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.change_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.comparison (
    version_id smallint NOT NULL,
    module text NOT NULL,
    "position" smallint NOT NULL,
    kind text NOT NULL,
    compare_version_id smallint,
    note text,
    CONSTRAINT ck_comparison_compare_version CHECK (((kind = 'version'::text) = (compare_version_id IS NOT NULL))),
    CONSTRAINT ck_comparison_kind CHECK ((kind = ANY (ARRAY['prior_year_actuals'::text, 'actuals'::text, 'version'::text])))
);

CREATE VIEW __SCHEMA__.credit_account AS
 SELECT DISTINCT a.member_id AS account_id
   FROM (__SCHEMA__.member t
     JOIN __SCHEMA__.member a ON (((a.dim_id = 3) AND ((a.code = t.account_code) OR (a.code = t.variable_account_code)))))
  WHERE (t.dim_id = 5);

CREATE TABLE __SCHEMA__.dataset_mapping (
    dataset text NOT NULL,
    query text NOT NULL,
    fields jsonb NOT NULL,
    hierarchy jsonb,
    updated_by text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_dataset_mapping_dataset CHECK ((dataset = ANY (ARRAY['entities'::text, 'cost_centers'::text, 'accounts'::text, 'actuals'::text, 'activity_types'::text, 'work_centers'::text, 'work_center_rates'::text, 'capacity_actuals'::text, 'staff_types'::text, 'staff_categories'::text, 'staff'::text, 'workforce_actuals'::text])))
);

CREATE TABLE __SCHEMA__.dimension (
    dim_id smallint NOT NULL,
    code text NOT NULL,
    name text NOT NULL
);

CREATE TABLE __SCHEMA__.edge_override (
    hier_id smallint NOT NULL,
    member_id integer NOT NULL,
    parent_id integer,
    set_by text,
    set_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE __SCHEMA__.entry_form (
    version_id smallint NOT NULL,
    module text NOT NULL,
    account_id integer NOT NULL,
    form text NOT NULL,
    profile_id smallint,
    reference_version_id smallint,
    CONSTRAINT ck_entry_form_form CHECK ((form = ANY (ARRAY['monthly'::text, 'annual'::text, 'annual_even'::text, 'annual_profile'::text, 'annual_reference'::text]))),
    CONSTRAINT ck_entry_form_one_basis CHECK (((profile_id IS NULL) OR (reference_version_id IS NULL))),
    CONSTRAINT ck_entry_form_profile CHECK (
CASE form
    WHEN 'annual_profile'::text THEN (profile_id IS NOT NULL)
    WHEN 'monthly'::text THEN true
    ELSE (profile_id IS NULL)
END),
    CONSTRAINT ck_entry_form_reference CHECK (((form = ANY (ARRAY['monthly'::text, 'annual_reference'::text])) OR (reference_version_id IS NULL)))
);

CREATE TABLE __SCHEMA__.fact_capacity (
    version_id smallint NOT NULL,
    layer_id smallint NOT NULL,
    measure_id smallint NOT NULL,
    period date NOT NULL,
    dim_entity integer NOT NULL,
    dim_cost_center integer NOT NULL,
    dim_work_center integer NOT NULL,
    dim_activity_type integer NOT NULL,
    amount numeric(18,2) NOT NULL,
    row_version bigint DEFAULT '1'::bigint NOT NULL
);

ALTER TABLE ONLY __SCHEMA__.fact_capacity FORCE ROW LEVEL SECURITY;

CREATE TABLE __SCHEMA__.fact_cost_center (
    version_id smallint NOT NULL,
    layer_id smallint NOT NULL,
    measure_id smallint NOT NULL,
    period date NOT NULL,
    dim_entity integer NOT NULL,
    dim_cost_center integer NOT NULL,
    dim_account integer NOT NULL,
    amount numeric(18,2) NOT NULL,
    row_version bigint DEFAULT '1'::bigint NOT NULL
);

ALTER TABLE ONLY __SCHEMA__.fact_cost_center FORCE ROW LEVEL SECURITY;

CREATE TABLE __SCHEMA__.fact_workforce (
    version_id smallint NOT NULL,
    layer_id smallint NOT NULL,
    measure_id smallint NOT NULL,
    period date NOT NULL,
    dim_entity integer NOT NULL,
    dim_cost_center integer NOT NULL,
    dim_staff_type integer NOT NULL,
    dim_staff_category integer NOT NULL,
    amount numeric(18,2) NOT NULL,
    row_version bigint DEFAULT '1'::bigint NOT NULL,
    CONSTRAINT ck_fact_workforce_amount CHECK ((amount >= (0)::numeric))
);

ALTER TABLE ONLY __SCHEMA__.fact_workforce FORCE ROW LEVEL SECURITY;

CREATE TABLE __SCHEMA__.forwarding (
    forwarding_id integer NOT NULL,
    version_id smallint NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    method text NOT NULL,
    source_id integer NOT NULL,
    shares text,
    amount numeric(18,2),
    rate numeric(18,4),
    unit text,
    charge_account_id integer NOT NULL,
    credit_account_id integer NOT NULL,
    changed_by text,
    changed_at timestamp with time zone DEFAULT now(),
    CONSTRAINT ck_forwarding_method CHECK ((method = ANY (ARRAY['full'::text, 'fixed'::text, 'rate'::text]))),
    CONSTRAINT ck_forwarding_method_values CHECK ((((method = 'full'::text) AND (shares IS NOT NULL) AND (amount IS NULL) AND (rate IS NULL)) OR ((method = 'fixed'::text) AND (shares IS NOT NULL) AND (amount >= (0)::numeric) AND (rate IS NULL)) OR ((method = 'rate'::text) AND (shares IS NULL) AND (amount IS NULL) AND (rate >= (0)::numeric)))),
    CONSTRAINT ck_forwarding_shares CHECK ((shares = ANY (ARRAY['percent'::text, 'portion'::text]))),
    CONSTRAINT ck_forwarding_two_accounts CHECK ((charge_account_id <> credit_account_id))
);

CREATE VIEW __SCHEMA__.forwarding_account AS
 SELECT forwarding.version_id,
    forwarding.charge_account_id AS account_id,
    'charge'::text AS side,
    forwarding.forwarding_id
   FROM __SCHEMA__.forwarding
UNION ALL
 SELECT forwarding.version_id,
    forwarding.credit_account_id AS account_id,
    'credit'::text AS side,
    forwarding.forwarding_id
   FROM __SCHEMA__.forwarding;

CREATE SEQUENCE __SCHEMA__.forwarding_forwarding_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE __SCHEMA__.forwarding_forwarding_id_seq OWNED BY __SCHEMA__.forwarding.forwarding_id;

CREATE TABLE __SCHEMA__.forwarding_receiver (
    forwarding_id integer NOT NULL,
    cost_center_id integer NOT NULL,
    quantity numeric(18,4) NOT NULL,
    CONSTRAINT ck_forwarding_receiver_quantity CHECK ((quantity >= (0)::numeric))
);

CREATE TABLE __SCHEMA__.hier_edge (
    hier_id smallint NOT NULL,
    member_id integer NOT NULL,
    parent_id integer,
    sort_order integer DEFAULT 0 NOT NULL,
    origin text DEFAULT 'source'::text NOT NULL,
    source_parent_id integer,
    CONSTRAINT ck_hier_edge_origin CHECK ((origin = ANY (ARRAY['source'::text, 'manual'::text])))
);

CREATE TABLE __SCHEMA__.hierarchy (
    hier_id smallint NOT NULL,
    dim_id smallint NOT NULL,
    code text NOT NULL,
    name text NOT NULL
);

CREATE TABLE __SCHEMA__.hr_pay (
    code text NOT NULL,
    cost_center_id integer NOT NULL,
    monthly_salary bytea NOT NULL
);

ALTER TABLE ONLY __SCHEMA__.hr_pay FORCE ROW LEVEL SECURITY;

CREATE TABLE __SCHEMA__.hr_staff (
    code text NOT NULL,
    cost_center_id integer NOT NULL,
    title text NOT NULL,
    person_name text,
    staff_type_id integer NOT NULL,
    staff_category_id integer NOT NULL,
    entry_date date,
    leave_date date,
    contract_percent numeric(5,2)
);

ALTER TABLE ONLY __SCHEMA__.hr_staff FORCE ROW LEVEL SECURITY;

CREATE TABLE __SCHEMA__.installation (
    id smallint DEFAULT '1'::smallint NOT NULL,
    fiscal_year_start_month smallint DEFAULT '1'::smallint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    published_signs text DEFAULT 'ledger'::text NOT NULL,
    pay_from_source boolean DEFAULT false NOT NULL,
    number_separators text DEFAULT 'comma_dot'::text NOT NULL,
    number_decimals boolean DEFAULT false NOT NULL,
    number_scale text DEFAULT 'full'::text NOT NULL,
    number_units boolean DEFAULT true NOT NULL,
    CONSTRAINT ck_installation_fiscal_month CHECK (((fiscal_year_start_month >= 1) AND (fiscal_year_start_month <= 12))),
    CONSTRAINT ck_installation_number_scale CHECK ((number_scale = ANY (ARRAY['full'::text, 'k'::text, 'M'::text]))),
    CONSTRAINT ck_installation_number_separators CHECK ((number_separators = ANY (ARRAY['comma_dot'::text, 'dot_comma'::text, 'apostrophe_dot'::text, 'space_comma'::text]))),
    CONSTRAINT ck_installation_single_row CHECK ((id = 1)),
    CONSTRAINT published_signs CHECK ((published_signs = ANY (ARRAY['ledger'::text, 'result'::text])))
);

CREATE TABLE __SCHEMA__.item (
    item_id bigint NOT NULL,
    list_id bigint NOT NULL,
    "position" integer NOT NULL,
    name text NOT NULL,
    supplier text,
    quantity numeric(18,4) DEFAULT '1'::numeric NOT NULL,
    unit_price numeric(18,2) NOT NULL,
    amount numeric(18,2) GENERATED ALWAYS AS (round((quantity * unit_price), 2)) STORED,
    comment text
);

ALTER TABLE ONLY __SCHEMA__.item FORCE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.item ALTER COLUMN item_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.item_item_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.item_list (
    list_id bigint NOT NULL,
    cost_center_id integer NOT NULL,
    account_id integer NOT NULL,
    owner_id integer NOT NULL,
    visibility text DEFAULT 'private'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    changed_by integer NOT NULL,
    CONSTRAINT ck_item_list_visibility CHECK ((visibility = ANY (ARRAY['private'::text, 'cost_center'::text, 'public'::text])))
);

ALTER TABLE ONLY __SCHEMA__.item_list FORCE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.item_list ALTER COLUMN list_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.item_list_list_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.line_spread (
    version_id smallint NOT NULL,
    module text NOT NULL,
    cost_center_id integer NOT NULL,
    account_id integer NOT NULL,
    basis text NOT NULL,
    profile_id smallint,
    changed_by text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_line_spread_basis CHECK ((basis = ANY (ARRAY['even'::text, 'reference'::text, 'profile'::text]))),
    CONSTRAINT ck_line_spread_profile CHECK (((basis = 'profile'::text) = (profile_id IS NOT NULL)))
);

ALTER TABLE ONLY __SCHEMA__.line_spread FORCE ROW LEVEL SECURITY;

CREATE TABLE __SCHEMA__.login_event (
    event_id bigint NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    login text NOT NULL,
    method text NOT NULL,
    outcome text NOT NULL,
    address text,
    user_agent text,
    detail text,
    user_id integer,
    CONSTRAINT login_event_method CHECK ((method = ANY (ARRAY['demo'::text, 'password'::text, 'oidc'::text, 'setup'::text]))),
    CONSTRAINT login_event_outcome CHECK ((outcome = ANY (ARRAY['success'::text, 'failed'::text, 'refused'::text, 'blocked'::text, 'error'::text])))
);

ALTER TABLE __SCHEMA__.login_event ALTER COLUMN event_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.login_event_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.login_setting (
    id smallint DEFAULT 1 NOT NULL,
    method text DEFAULT 'none'::text NOT NULL,
    oidc_label text DEFAULT ''::text NOT NULL,
    oidc_issuer text DEFAULT ''::text NOT NULL,
    oidc_client_id text DEFAULT ''::text NOT NULL,
    oidc_scopes text DEFAULT 'openid profile email'::text NOT NULL,
    oidc_login_claim text DEFAULT 'preferred_username'::text NOT NULL,
    oidc_email_claim text DEFAULT 'email'::text NOT NULL,
    oidc_name_claim text DEFAULT 'name'::text NOT NULL,
    oidc_groups_claim text DEFAULT 'groups'::text NOT NULL,
    ldap_url text DEFAULT ''::text NOT NULL,
    ldap_bind_dn text DEFAULT ''::text NOT NULL,
    ldap_base_dn text DEFAULT ''::text NOT NULL,
    ldap_login_attribute text DEFAULT 'uid'::text NOT NULL,
    ldap_email_attribute text DEFAULT 'mail'::text NOT NULL,
    ldap_name_attribute text DEFAULT 'cn'::text NOT NULL,
    ca_pem text DEFAULT ''::text NOT NULL,
    allowed_group text DEFAULT ''::text NOT NULL,
    admin_group text DEFAULT ''::text NOT NULL,
    controller_group text DEFAULT ''::text NOT NULL,
    viewer_group text DEFAULT ''::text NOT NULL,
    hr_group text DEFAULT ''::text NOT NULL,
    setup_code_hash text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    oidc_hosted_domain text DEFAULT ''::text NOT NULL,
    oidc_client_secret bytea,
    CONSTRAINT login_method CHECK ((method = ANY (ARRAY['none'::text, 'oidc'::text, 'ldap'::text]))),
    CONSTRAINT single_row CHECK ((id = 1))
);

CREATE SEQUENCE __SCHEMA__.member_member_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE __SCHEMA__.member_member_id_seq OWNED BY __SCHEMA__.member.member_id;

CREATE TABLE __SCHEMA__.member_override (
    member_id integer NOT NULL,
    name text NOT NULL,
    set_by text,
    set_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE __SCHEMA__.member_shadow (
    id bigint NOT NULL,
    member_id integer NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    attributes jsonb NOT NULL,
    parents jsonb NOT NULL,
    confirmed_by text,
    confirmed_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE __SCHEMA__.member_shadow ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.member_shadow_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.not_planned_account (
    version_id smallint CONSTRAINT hidden_account_version_id_not_null NOT NULL,
    module text CONSTRAINT hidden_account_module_not_null NOT NULL,
    account_id integer CONSTRAINT hidden_account_account_id_not_null NOT NULL
);

CREATE TABLE __SCHEMA__.note (
    note_id bigint NOT NULL,
    thread_id bigint NOT NULL,
    author_id integer NOT NULL,
    body text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    edited_at timestamp with time zone
);

ALTER TABLE ONLY __SCHEMA__.note FORCE ROW LEVEL SECURITY;

CREATE TABLE __SCHEMA__.note_grid (
    grid_id bigint NOT NULL,
    module text NOT NULL,
    cost_center_id integer NOT NULL,
    account_id integer,
    work_center_id integer,
    activity_type_id integer,
    round_id smallint NOT NULL,
    rows jsonb NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    changed_by integer NOT NULL,
    CONSTRAINT ck_note_grid_line CHECK ((((module = 'cost_center'::text) AND (account_id IS NOT NULL) AND (work_center_id IS NULL) AND (activity_type_id IS NULL)) OR ((module = 'capacity'::text) AND (account_id IS NULL) AND (work_center_id IS NOT NULL) AND (activity_type_id IS NOT NULL))))
);

ALTER TABLE ONLY __SCHEMA__.note_grid FORCE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.note_grid ALTER COLUMN grid_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.note_grid_grid_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.note_mention (
    note_id bigint NOT NULL,
    user_id integer NOT NULL
);

ALTER TABLE ONLY __SCHEMA__.note_mention FORCE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.note ALTER COLUMN note_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.note_note_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.note_thread (
    thread_id bigint NOT NULL,
    module text NOT NULL,
    cost_center_id integer,
    account_id integer,
    work_center_id integer,
    activity_type_id integer,
    round_id smallint NOT NULL,
    assignee_id integer,
    resolved_at timestamp with time zone,
    resolved_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by integer NOT NULL,
    capex_item_id integer,
    CONSTRAINT ck_note_thread_line CHECK ((((((module = 'cost_center'::text) AND (account_id IS NOT NULL) AND (work_center_id IS NULL) AND (activity_type_id IS NULL)) OR ((module = 'capacity'::text) AND (account_id IS NULL) AND (work_center_id IS NOT NULL) AND (activity_type_id IS NOT NULL))) AND (cost_center_id IS NOT NULL) AND (capex_item_id IS NULL)) OR ((module = 'capex'::text) AND (capex_item_id IS NOT NULL) AND (cost_center_id IS NULL) AND (account_id IS NULL) AND (work_center_id IS NULL) AND (activity_type_id IS NULL))))
);

ALTER TABLE ONLY __SCHEMA__.note_thread FORCE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.note_thread ALTER COLUMN thread_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.note_thread_thread_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.oidc_login (
    state text NOT NULL,
    nonce text NOT NULL,
    verifier text NOT NULL,
    redirect_uri text NOT NULL,
    test_by integer,
    settings jsonb,
    result jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE __SCHEMA__.pay_reader (
    version_id smallint NOT NULL,
    module text DEFAULT 'personnel_costs'::text NOT NULL,
    cost_center_id integer NOT NULL,
    user_id integer NOT NULL,
    added_by text,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_pay_reader_module CHECK ((module = 'personnel_costs'::text))
);

CREATE TABLE __SCHEMA__.pay_release (
    version_id smallint NOT NULL,
    module text DEFAULT 'personnel_costs'::text NOT NULL,
    released_by text NOT NULL,
    released_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_pay_release_module CHECK ((module = 'personnel_costs'::text))
);

CREATE TABLE __SCHEMA__.round_module (
    version_id smallint NOT NULL,
    module text NOT NULL,
    default_form text DEFAULT 'monthly'::text NOT NULL,
    mode text,
    fte_hours numeric(7,2),
    CONSTRAINT ck_round_module_default_form CHECK ((default_form = ANY (ARRAY['monthly'::text, 'annual'::text, 'annual_even'::text, 'annual_profile'::text, 'annual_reference'::text]))),
    CONSTRAINT ck_round_module_fte_hours CHECK (((fte_hours > (0)::numeric) AND ((fte_hours IS NULL) OR (module = 'capacity'::text)))),
    CONSTRAINT ck_round_module_mode CHECK ((mode = ANY (ARRAY['fte'::text, 'headcount'::text, 'both'::text, 'positions'::text]))),
    CONSTRAINT ck_round_module_mode_workforce CHECK (((module = 'workforce'::text) = (mode IS NOT NULL))),
    CONSTRAINT ck_round_module_module CHECK ((module = ANY (ARRAY['cost_center'::text, 'capacity'::text, 'workforce'::text, 'personnel_costs'::text, 'capex'::text])))
);

CREATE VIEW __SCHEMA__.pay_access AS
 SELECT u.user_id,
    r.version_id,
    m.member_id AS cost_center_id
   FROM ((__SCHEMA__.app_user u
     CROSS JOIN __SCHEMA__.round_module r)
     CROSS JOIN __SCHEMA__.member m)
  WHERE ((u.is_hr OR u.is_system) AND (NOT u.disabled) AND (r.module = 'personnel_costs'::text) AND (m.dim_id = 2) AND (NOT m.is_group))
UNION
 SELECT p.user_id,
    p.version_id,
    p.cost_center_id
   FROM ((__SCHEMA__.pay_reader p
     JOIN __SCHEMA__.pay_release x ON ((x.version_id = p.version_id)))
     JOIN __SCHEMA__.app_user u ON ((u.user_id = p.user_id)))
  WHERE (NOT u.disabled);

CREATE TABLE __SCHEMA__.pay_access_log (
    id bigint NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    actor text,
    action text NOT NULL,
    version_id smallint,
    cost_center_id integer,
    user_id integer
);

ALTER TABLE __SCHEMA__.pay_access_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.pay_access_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.pay_rule (
    rule_id integer NOT NULL,
    version_id smallint NOT NULL,
    module text DEFAULT 'personnel_costs'::text NOT NULL,
    kind text NOT NULL,
    name text NOT NULL,
    account_id integer NOT NULL,
    entity_id integer,
    salaries numeric(8,4),
    percent numeric(7,4),
    ceiling numeric(18,2),
    starts date,
    changed_by text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_pay_rule_ceiling CHECK (((ceiling IS NULL) OR ((kind = 'contribution'::text) AND (ceiling > (0)::numeric)))),
    CONSTRAINT ck_pay_rule_kind CHECK ((kind = ANY (ARRAY['extra'::text, 'raise'::text, 'award'::text, 'contribution'::text]))),
    CONSTRAINT ck_pay_rule_module CHECK ((module = 'personnel_costs'::text)),
    CONSTRAINT ck_pay_rule_name CHECK ((name <> ''::text)),
    CONSTRAINT ck_pay_rule_percent CHECK ((((kind = ANY (ARRAY['raise'::text, 'contribution'::text])) = (percent IS NOT NULL)) AND (COALESCE(percent, (1)::numeric) > (0)::numeric))),
    CONSTRAINT ck_pay_rule_salaries CHECK ((((kind = 'extra'::text) = (salaries IS NOT NULL)) AND (COALESCE(salaries, (1)::numeric) > (0)::numeric))),
    CONSTRAINT ck_pay_rule_starts CHECK (((kind = 'raise'::text) = (starts IS NOT NULL)))
);

CREATE TABLE __SCHEMA__.pay_setting (
    version_id smallint NOT NULL,
    module text DEFAULT 'personnel_costs'::text NOT NULL,
    min_group smallint DEFAULT '3'::smallint NOT NULL,
    bonus_account_id integer,
    changed_by text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    review_required boolean DEFAULT false NOT NULL,
    CONSTRAINT ck_pay_setting_min_group CHECK ((min_group >= 1)),
    CONSTRAINT ck_pay_setting_module CHECK ((module = 'personnel_costs'::text))
);

CREATE TABLE __SCHEMA__.pay_staff_type (
    version_id smallint NOT NULL,
    module text DEFAULT 'personnel_costs'::text NOT NULL,
    staff_type_id integer NOT NULL,
    account_id integer NOT NULL,
    typed boolean NOT NULL,
    CONSTRAINT ck_pay_staff_type_module CHECK ((module = 'personnel_costs'::text))
);

CREATE VIEW __SCHEMA__.pay_account AS
 SELECT pay_staff_type.version_id,
    pay_staff_type.account_id
   FROM __SCHEMA__.pay_staff_type
UNION
 SELECT pay_rule.version_id,
    pay_rule.account_id
   FROM __SCHEMA__.pay_rule
UNION
 SELECT pay_setting.version_id,
    pay_setting.bonus_account_id AS account_id
   FROM __SCHEMA__.pay_setting
  WHERE (pay_setting.bonus_account_id IS NOT NULL);

CREATE TABLE __SCHEMA__.pay_handover (
    version_id smallint NOT NULL,
    module text DEFAULT 'personnel_costs'::text NOT NULL,
    cost_center_id integer NOT NULL,
    handed_by text NOT NULL,
    handed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_pay_handover_module CHECK ((module = 'personnel_costs'::text))
);

CREATE TABLE __SCHEMA__.pay_owner_seen (
    version_id smallint NOT NULL,
    module text DEFAULT 'personnel_costs'::text NOT NULL,
    cost_center_id integer NOT NULL,
    owner_id integer,
    CONSTRAINT ck_pay_owner_seen_module CHECK ((module = 'personnel_costs'::text))
);

CREATE TABLE __SCHEMA__.pay_review (
    version_id smallint NOT NULL,
    cost_center_id integer NOT NULL,
    confirmed_by text NOT NULL,
    confirmed_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE __SCHEMA__.pay_rule ALTER COLUMN rule_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.pay_rule_rule_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.pay_rule_step (
    rule_id integer NOT NULL,
    years smallint NOT NULL,
    salaries numeric(8,4),
    amount numeric(18,2),
    CONSTRAINT ck_pay_rule_step_one CHECK ((((salaries IS NULL) <> (amount IS NULL)) AND (COALESCE(salaries, amount) > (0)::numeric))),
    CONSTRAINT ck_pay_rule_step_years CHECK ((years > 0))
);

CREATE TABLE __SCHEMA__.pay_small_group (
    version_id smallint NOT NULL,
    cost_center_id integer NOT NULL,
    account_id integer NOT NULL,
    positions smallint NOT NULL
);

CREATE TABLE __SCHEMA__.planning_round (
    version_id smallint NOT NULL,
    fiscal_year integer NOT NULL,
    deadline date,
    status text DEFAULT 'setup'::text NOT NULL,
    copied_from smallint,
    created_by text,
    created_at timestamp with time zone,
    carried jsonb,
    grain text DEFAULT 'month'::text NOT NULL,
    actuals_through date,
    CONSTRAINT ck_planning_round_status CHECK ((status = ANY (ARRAY['setup'::text, 'open'::text, 'closed'::text]))),
    CONSTRAINT planning_round_actuals_through_check CHECK ((actuals_through = (date_trunc('month'::text, (actuals_through)::timestamp with time zone))::date)),
    CONSTRAINT planning_round_grain_check CHECK ((grain = ANY (ARRAY['month'::text, 'quarter'::text])))
);

CREATE TABLE __SCHEMA__.position_pay (
    position_id integer NOT NULL,
    version_id smallint NOT NULL,
    module text DEFAULT 'personnel_costs'::text NOT NULL,
    charged_to_id integer,
    changed_by text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    excluded_rules integer[] DEFAULT '{}'::integer[] NOT NULL,
    hr_salary bytea,
    salary bytea,
    bonus bytea,
    CONSTRAINT ck_position_pay_module CHECK ((module = 'personnel_costs'::text))
);

ALTER TABLE ONLY __SCHEMA__.position_pay FORCE ROW LEVEL SECURITY;

CREATE TABLE __SCHEMA__.position_pay_log (
    id bigint NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    actor text,
    batch_id text,
    op text NOT NULL,
    version_id smallint NOT NULL,
    cost_center_id integer NOT NULL,
    position_id integer NOT NULL,
    old jsonb,
    new jsonb
);

ALTER TABLE ONLY __SCHEMA__.position_pay_log FORCE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.position_pay_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.position_pay_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.publish_run (
    id bigint NOT NULL,
    version_id smallint NOT NULL,
    scope text NOT NULL,
    actor text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    rows integer,
    added integer,
    changed integer,
    removed integer,
    fingerprint text,
    CONSTRAINT ck_publish_run_scope CHECK ((scope = ANY (ARRAY['approved'::text, 'all'::text])))
);

ALTER TABLE __SCHEMA__.publish_run ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.publish_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.round_member (
    version_id smallint NOT NULL,
    module text NOT NULL,
    member_id integer NOT NULL,
    enabled boolean NOT NULL
);

CREATE TABLE __SCHEMA__.seasonality_profile (
    profile_id smallint NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    entered_as text NOT NULL,
    "values" numeric[] NOT NULL,
    CONSTRAINT ck_seasonality_profile_entered_as CHECK ((entered_as = ANY (ARRAY['percent'::text, 'relative'::text]))),
    CONSTRAINT ck_seasonality_profile_twelve_values CHECK ((cardinality("values") = 12))
);

ALTER TABLE __SCHEMA__.seasonality_profile ALTER COLUMN profile_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.seasonality_profile_profile_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.sheet_account (
    version_id smallint NOT NULL,
    module text NOT NULL,
    cost_center_id integer NOT NULL,
    account_id integer NOT NULL,
    added_by text,
    added_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE __SCHEMA__.sync_run (
    id bigint NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    actor text,
    entity_filter text,
    members integer,
    actual_rows integer,
    summary jsonb
);

ALTER TABLE __SCHEMA__.sync_run ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.sync_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.user_session (
    token_hash text NOT NULL,
    user_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL
);

CREATE TABLE __SCHEMA__.version (
    version_id smallint NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    kind text NOT NULL,
    CONSTRAINT ck_version_kind CHECK ((kind = ANY (ARRAY['actuals'::text, 'budget'::text, 'forecast'::text])))
);

CREATE SEQUENCE __SCHEMA__.version_version_id_seq
    AS smallint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE __SCHEMA__.version_version_id_seq OWNED BY __SCHEMA__.version.version_id;

CREATE TABLE __SCHEMA__.work_center_rate (
    work_center_id integer NOT NULL,
    activity_type_id integer NOT NULL,
    rate numeric(18,4) NOT NULL
);

CREATE TABLE __SCHEMA__.workflow_event (
    id bigint NOT NULL,
    version_id smallint NOT NULL,
    cost_center_id integer NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    actor text,
    from_status text NOT NULL,
    to_status text NOT NULL,
    comment text
);

ALTER TABLE __SCHEMA__.workflow_event ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.workflow_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.workflow_status (
    version_id smallint NOT NULL,
    cost_center_id integer NOT NULL,
    status text NOT NULL,
    changed_at timestamp with time zone,
    changed_by text,
    CONSTRAINT ck_workflow_status_status CHECK ((status = ANY (ARRAY['not_started'::text, 'in_progress'::text, 'submitted'::text, 'approved'::text])))
);

CREATE TABLE __SCHEMA__.workforce_change_log (
    id bigint NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    actor text,
    batch_id text,
    op text NOT NULL,
    version_id smallint NOT NULL,
    measure_id smallint NOT NULL,
    period date NOT NULL,
    dim_cost_center integer NOT NULL,
    dim_staff_type integer NOT NULL,
    dim_staff_category integer NOT NULL,
    old_amount numeric(18,2),
    new_amount numeric(18,2)
);

ALTER TABLE __SCHEMA__.workforce_change_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.workforce_change_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.workforce_line (
    version_id smallint NOT NULL,
    module text DEFAULT 'workforce'::text NOT NULL,
    cost_center_id integer NOT NULL,
    staff_type_id integer NOT NULL,
    staff_category_id integer NOT NULL,
    changed_by text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_workforce_line_module CHECK ((module = 'workforce'::text))
);

ALTER TABLE ONLY __SCHEMA__.workforce_line FORCE ROW LEVEL SECURITY;

CREATE TABLE __SCHEMA__.workforce_position (
    position_id integer NOT NULL,
    version_id smallint NOT NULL,
    module text DEFAULT 'workforce'::text NOT NULL,
    cost_center_id integer NOT NULL,
    code text NOT NULL,
    title text NOT NULL,
    person_name text,
    staff_type_id integer NOT NULL,
    staff_category_id integer NOT NULL,
    entry_date date,
    leave_date date,
    contract_percent numeric(5,2),
    is_plan boolean DEFAULT false NOT NULL,
    is_replacement boolean DEFAULT false NOT NULL,
    description text,
    changed_by text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    origin text DEFAULT 'manual'::text NOT NULL,
    transferred_from_id integer,
    transferred_to_id integer,
    CONSTRAINT ck_workforce_position_contract CHECK (((contract_percent > (0)::numeric) AND (contract_percent <= (100)::numeric))),
    CONSTRAINT ck_workforce_position_dates CHECK ((leave_date >= entry_date)),
    CONSTRAINT ck_workforce_position_hr_staff CHECK (((origin = 'manual'::text) OR (NOT is_plan))),
    CONSTRAINT ck_workforce_position_module CHECK ((module = 'workforce'::text)),
    CONSTRAINT ck_workforce_position_number CHECK (((origin = 'hr'::text) OR (code ~ '^N[0-9]{5,}$'::text))),
    CONSTRAINT ck_workforce_position_origin CHECK ((origin = ANY (ARRAY['hr'::text, 'manual'::text]))),
    CONSTRAINT ck_workforce_position_replacement CHECK ((is_plan OR (NOT is_replacement))),
    CONSTRAINT ck_workforce_position_title CHECK ((title <> ''::text))
);

ALTER TABLE ONLY __SCHEMA__.workforce_position FORCE ROW LEVEL SECURITY;

CREATE TABLE __SCHEMA__.workforce_position_log (
    id bigint NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    actor text,
    batch_id text,
    op text NOT NULL,
    version_id smallint NOT NULL,
    cost_center_id integer NOT NULL,
    position_id integer NOT NULL,
    old jsonb,
    new jsonb
);

ALTER TABLE __SCHEMA__.workforce_position_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.workforce_position_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE __SCHEMA__.workforce_position ALTER COLUMN position_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.workforce_position_position_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE __SCHEMA__.workforce_transfer (
    transfer_id integer NOT NULL,
    version_id smallint NOT NULL,
    module text DEFAULT 'workforce'::text NOT NULL,
    position_id integer NOT NULL,
    from_cost_center_id integer NOT NULL,
    to_cost_center_id integer NOT NULL,
    kind text NOT NULL,
    starts date,
    status text DEFAULT 'proposed'::text NOT NULL,
    note text,
    proposed_by integer NOT NULL,
    proposed_at timestamp with time zone DEFAULT now() NOT NULL,
    decided_by integer,
    decided_at timestamp with time zone,
    reason text,
    new_position_id integer,
    CONSTRAINT ck_workforce_transfer_decided CHECK (((status = 'proposed'::text) = (decided_at IS NULL))),
    CONSTRAINT ck_workforce_transfer_first_day CHECK (((starts IS NULL) OR (EXTRACT(day FROM starts) = (1)::numeric))),
    CONSTRAINT ck_workforce_transfer_kind CHECK ((kind = ANY (ARRAY['full'::text, 'during_year'::text]))),
    CONSTRAINT ck_workforce_transfer_module CHECK ((module = 'workforce'::text)),
    CONSTRAINT ck_workforce_transfer_other CHECK ((from_cost_center_id <> to_cost_center_id)),
    CONSTRAINT ck_workforce_transfer_starts CHECK (((kind = 'full'::text) = (starts IS NULL))),
    CONSTRAINT ck_workforce_transfer_status CHECK ((status = ANY (ARRAY['proposed'::text, 'accepted'::text, 'declined'::text, 'cancelled'::text])))
);

ALTER TABLE ONLY __SCHEMA__.workforce_transfer FORCE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.workforce_transfer ALTER COLUMN transfer_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME __SCHEMA__.workforce_transfer_transfer_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE VIEW __SCHEMA__.written_account AS
 SELECT DISTINCT forwarding_account.version_id,
    forwarding_account.account_id,
    'forwarding'::text AS source
   FROM __SCHEMA__.forwarding_account
UNION ALL
 SELECT pay_account.version_id,
    pay_account.account_id,
    'personnel'::text AS source
   FROM __SCHEMA__.pay_account;

ALTER TABLE ONLY __SCHEMA__.forwarding ALTER COLUMN forwarding_id SET DEFAULT nextval('__SCHEMA__.forwarding_forwarding_id_seq'::regclass);

ALTER TABLE ONLY __SCHEMA__.member ALTER COLUMN member_id SET DEFAULT nextval('__SCHEMA__.member_member_id_seq'::regclass);

ALTER TABLE ONLY __SCHEMA__.version ALTER COLUMN version_id SET DEFAULT nextval('__SCHEMA__.version_version_id_seq'::regclass);

ALTER TABLE ONLY __SCHEMA__.app_user
    ADD CONSTRAINT app_user_identity_key UNIQUE (identity);

ALTER TABLE ONLY __SCHEMA__.login_event
    ADD CONSTRAINT login_event_pkey PRIMARY KEY (event_id);

ALTER TABLE ONLY __SCHEMA__.login_setting
    ADD CONSTRAINT login_setting_pkey PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.oidc_login
    ADD CONSTRAINT oidc_login_pkey PRIMARY KEY (state);

ALTER TABLE ONLY __SCHEMA__.account_set
    ADD CONSTRAINT pk_account_set PRIMARY KEY (set_id);

ALTER TABLE ONLY __SCHEMA__.account_set_account
    ADD CONSTRAINT pk_account_set_account PRIMARY KEY (set_id, account_id);

ALTER TABLE ONLY __SCHEMA__.account_set_assignment
    ADD CONSTRAINT pk_account_set_assignment PRIMARY KEY (version_id, module, member_id);

ALTER TABLE ONLY __SCHEMA__.annotation_log
    ADD CONSTRAINT pk_annotation_log PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.app_user
    ADD CONSTRAINT pk_app_user PRIMARY KEY (user_id);

ALTER TABLE ONLY __SCHEMA__.approver_assignment
    ADD CONSTRAINT pk_approver_assignment PRIMARY KEY (member_id, user_id);

ALTER TABLE ONLY __SCHEMA__.capacity_change_log
    ADD CONSTRAINT pk_capacity_change_log PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.capacity_grant
    ADD CONSTRAINT pk_capacity_grant PRIMARY KEY (member_id, user_id);

ALTER TABLE ONLY __SCHEMA__.capacity_line
    ADD CONSTRAINT pk_capacity_line PRIMARY KEY (version_id, work_center_id, activity_type_id);

ALTER TABLE ONLY __SCHEMA__.capacity_line_log
    ADD CONSTRAINT pk_capacity_line_log PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.capacity_position
    ADD CONSTRAINT pk_capacity_position PRIMARY KEY (position_id);

ALTER TABLE ONLY __SCHEMA__.capacity_staff_log
    ADD CONSTRAINT pk_capacity_staff_log PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.capacity_unit
    ADD CONSTRAINT pk_capacity_unit PRIMARY KEY (version_id, cost_center_id);

ALTER TABLE ONLY __SCHEMA__.capex_cash_out
    ADD CONSTRAINT pk_capex_cash_out PRIMARY KEY (item_id, period);

ALTER TABLE ONLY __SCHEMA__.capex_item
    ADD CONSTRAINT pk_capex_item PRIMARY KEY (item_id);

ALTER TABLE ONLY __SCHEMA__.capex_log
    ADD CONSTRAINT pk_capex_log PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.capex_setting
    ADD CONSTRAINT pk_capex_setting PRIMARY KEY (version_id);

ALTER TABLE ONLY __SCHEMA__.capex_type
    ADD CONSTRAINT pk_capex_type PRIMARY KEY (type_id);

ALTER TABLE ONLY __SCHEMA__.change_log
    ADD CONSTRAINT pk_change_log PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.closure
    ADD CONSTRAINT pk_closure PRIMARY KEY (hier_id, ancestor_id, descendant_id);

ALTER TABLE ONLY __SCHEMA__.comparison
    ADD CONSTRAINT pk_comparison PRIMARY KEY (version_id, module, "position");

ALTER TABLE ONLY __SCHEMA__.dataset_mapping
    ADD CONSTRAINT pk_dataset_mapping PRIMARY KEY (dataset);

ALTER TABLE ONLY __SCHEMA__.dimension
    ADD CONSTRAINT pk_dimension PRIMARY KEY (dim_id);

ALTER TABLE ONLY __SCHEMA__.edge_override
    ADD CONSTRAINT pk_edge_override PRIMARY KEY (hier_id, member_id);

ALTER TABLE ONLY __SCHEMA__.entry_form
    ADD CONSTRAINT pk_entry_form PRIMARY KEY (version_id, module, account_id);

ALTER TABLE ONLY __SCHEMA__.fact_capacity
    ADD CONSTRAINT pk_fact_capacity PRIMARY KEY (version_id, layer_id, measure_id, period, dim_entity, dim_cost_center, dim_work_center, dim_activity_type);

ALTER TABLE ONLY __SCHEMA__.fact_cost_center
    ADD CONSTRAINT pk_fact_cost_center PRIMARY KEY (version_id, layer_id, measure_id, period, dim_entity, dim_cost_center, dim_account);

ALTER TABLE ONLY __SCHEMA__.fact_workforce
    ADD CONSTRAINT pk_fact_workforce PRIMARY KEY (version_id, layer_id, measure_id, period, dim_entity, dim_cost_center, dim_staff_type, dim_staff_category);

ALTER TABLE ONLY __SCHEMA__.forwarding
    ADD CONSTRAINT pk_forwarding PRIMARY KEY (forwarding_id);

ALTER TABLE ONLY __SCHEMA__.forwarding_receiver
    ADD CONSTRAINT pk_forwarding_receiver PRIMARY KEY (forwarding_id, cost_center_id);

ALTER TABLE ONLY __SCHEMA__.not_planned_account
    ADD CONSTRAINT pk_hidden_account PRIMARY KEY (version_id, module, account_id);

ALTER TABLE ONLY __SCHEMA__.hier_edge
    ADD CONSTRAINT pk_hier_edge PRIMARY KEY (hier_id, member_id);

ALTER TABLE ONLY __SCHEMA__.hierarchy
    ADD CONSTRAINT pk_hierarchy PRIMARY KEY (hier_id);

ALTER TABLE ONLY __SCHEMA__.hr_pay
    ADD CONSTRAINT pk_hr_pay PRIMARY KEY (code);

ALTER TABLE ONLY __SCHEMA__.hr_staff
    ADD CONSTRAINT pk_hr_staff PRIMARY KEY (code);

ALTER TABLE ONLY __SCHEMA__.installation
    ADD CONSTRAINT pk_installation PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.item
    ADD CONSTRAINT pk_item PRIMARY KEY (item_id);

ALTER TABLE ONLY __SCHEMA__.item_list
    ADD CONSTRAINT pk_item_list PRIMARY KEY (list_id);

ALTER TABLE ONLY __SCHEMA__.line_spread
    ADD CONSTRAINT pk_line_spread PRIMARY KEY (version_id, module, cost_center_id, account_id);

ALTER TABLE ONLY __SCHEMA__.member
    ADD CONSTRAINT pk_member PRIMARY KEY (member_id);

ALTER TABLE ONLY __SCHEMA__.member_override
    ADD CONSTRAINT pk_member_override PRIMARY KEY (member_id);

ALTER TABLE ONLY __SCHEMA__.member_shadow
    ADD CONSTRAINT pk_member_shadow PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.note
    ADD CONSTRAINT pk_note PRIMARY KEY (note_id);

ALTER TABLE ONLY __SCHEMA__.note_grid
    ADD CONSTRAINT pk_note_grid PRIMARY KEY (grid_id);

ALTER TABLE ONLY __SCHEMA__.note_mention
    ADD CONSTRAINT pk_note_mention PRIMARY KEY (note_id, user_id);

ALTER TABLE ONLY __SCHEMA__.note_thread
    ADD CONSTRAINT pk_note_thread PRIMARY KEY (thread_id);

ALTER TABLE ONLY __SCHEMA__.owner_override
    ADD CONSTRAINT pk_owner_override PRIMARY KEY (cost_center_id);

ALTER TABLE ONLY __SCHEMA__.pay_access_log
    ADD CONSTRAINT pk_pay_access_log PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.pay_handover
    ADD CONSTRAINT pk_pay_handover PRIMARY KEY (version_id, cost_center_id);

ALTER TABLE ONLY __SCHEMA__.pay_owner_seen
    ADD CONSTRAINT pk_pay_owner_seen PRIMARY KEY (version_id, cost_center_id);

ALTER TABLE ONLY __SCHEMA__.pay_reader
    ADD CONSTRAINT pk_pay_reader PRIMARY KEY (version_id, cost_center_id, user_id);

ALTER TABLE ONLY __SCHEMA__.pay_release
    ADD CONSTRAINT pk_pay_release PRIMARY KEY (version_id);

ALTER TABLE ONLY __SCHEMA__.pay_review
    ADD CONSTRAINT pk_pay_review PRIMARY KEY (version_id, cost_center_id);

ALTER TABLE ONLY __SCHEMA__.pay_rule
    ADD CONSTRAINT pk_pay_rule PRIMARY KEY (rule_id);

ALTER TABLE ONLY __SCHEMA__.pay_rule_step
    ADD CONSTRAINT pk_pay_rule_step PRIMARY KEY (rule_id, years);

ALTER TABLE ONLY __SCHEMA__.pay_setting
    ADD CONSTRAINT pk_pay_setting PRIMARY KEY (version_id);

ALTER TABLE ONLY __SCHEMA__.pay_small_group
    ADD CONSTRAINT pk_pay_small_group PRIMARY KEY (version_id, cost_center_id, account_id);

ALTER TABLE ONLY __SCHEMA__.pay_staff_type
    ADD CONSTRAINT pk_pay_staff_type PRIMARY KEY (version_id, staff_type_id);

ALTER TABLE ONLY __SCHEMA__.planner_grant
    ADD CONSTRAINT pk_planner_grant PRIMARY KEY (member_id, user_id);

ALTER TABLE ONLY __SCHEMA__.planning_round
    ADD CONSTRAINT pk_planning_round PRIMARY KEY (version_id);

ALTER TABLE ONLY __SCHEMA__.position_pay
    ADD CONSTRAINT pk_position_pay PRIMARY KEY (position_id);

ALTER TABLE ONLY __SCHEMA__.position_pay_log
    ADD CONSTRAINT pk_position_pay_log PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.publish_run
    ADD CONSTRAINT pk_publish_run PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.round_member
    ADD CONSTRAINT pk_round_member PRIMARY KEY (version_id, module, member_id);

ALTER TABLE ONLY __SCHEMA__.round_module
    ADD CONSTRAINT pk_round_module PRIMARY KEY (version_id, module);

ALTER TABLE ONLY __SCHEMA__.seasonality_profile
    ADD CONSTRAINT pk_seasonality_profile PRIMARY KEY (profile_id);

ALTER TABLE ONLY __SCHEMA__.sheet_account
    ADD CONSTRAINT pk_sheet_account PRIMARY KEY (version_id, module, cost_center_id, account_id);

ALTER TABLE ONLY __SCHEMA__.sync_run
    ADD CONSTRAINT pk_sync_run PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.user_session
    ADD CONSTRAINT pk_user_session PRIMARY KEY (token_hash);

ALTER TABLE ONLY __SCHEMA__.version
    ADD CONSTRAINT pk_version PRIMARY KEY (version_id);

ALTER TABLE ONLY __SCHEMA__.work_center_rate
    ADD CONSTRAINT pk_work_center_rate PRIMARY KEY (work_center_id, activity_type_id);

ALTER TABLE ONLY __SCHEMA__.workflow_event
    ADD CONSTRAINT pk_workflow_event PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.workflow_status
    ADD CONSTRAINT pk_workflow_status PRIMARY KEY (version_id, cost_center_id);

ALTER TABLE ONLY __SCHEMA__.workforce_change_log
    ADD CONSTRAINT pk_workforce_change_log PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.workforce_line
    ADD CONSTRAINT pk_workforce_line PRIMARY KEY (version_id, cost_center_id, staff_type_id, staff_category_id);

ALTER TABLE ONLY __SCHEMA__.workforce_position
    ADD CONSTRAINT pk_workforce_position PRIMARY KEY (position_id);

ALTER TABLE ONLY __SCHEMA__.workforce_position_log
    ADD CONSTRAINT pk_workforce_position_log PRIMARY KEY (id);

ALTER TABLE ONLY __SCHEMA__.workforce_transfer
    ADD CONSTRAINT pk_workforce_transfer PRIMARY KEY (transfer_id);

ALTER TABLE ONLY __SCHEMA__.account_set
    ADD CONSTRAINT uq_account_set_version_id_module_name UNIQUE (version_id, module, name);

ALTER TABLE ONLY __SCHEMA__.app_user
    ADD CONSTRAINT uq_app_user_login UNIQUE (login);

ALTER TABLE ONLY __SCHEMA__.capex_item
    ADD CONSTRAINT uq_capex_item_cost_center UNIQUE (item_id, cost_center_id);

ALTER TABLE ONLY __SCHEMA__.capex_type
    ADD CONSTRAINT uq_capex_type_code UNIQUE (code);

ALTER TABLE ONLY __SCHEMA__.dimension
    ADD CONSTRAINT uq_dimension_code UNIQUE (code);

ALTER TABLE ONLY __SCHEMA__.forwarding
    ADD CONSTRAINT uq_forwarding_version_id_charge_account_id UNIQUE (version_id, charge_account_id);

ALTER TABLE ONLY __SCHEMA__.forwarding
    ADD CONSTRAINT uq_forwarding_version_id_code UNIQUE (version_id, code);

ALTER TABLE ONLY __SCHEMA__.forwarding
    ADD CONSTRAINT uq_forwarding_version_id_credit_account_id UNIQUE (version_id, credit_account_id);

ALTER TABLE ONLY __SCHEMA__.hierarchy
    ADD CONSTRAINT uq_hierarchy_code UNIQUE (code);

ALTER TABLE ONLY __SCHEMA__.item_list
    ADD CONSTRAINT uq_item_list_cost_center_id_account_id UNIQUE (cost_center_id, account_id);

ALTER TABLE ONLY __SCHEMA__.member
    ADD CONSTRAINT uq_member_dim_id_code UNIQUE (dim_id, code);

ALTER TABLE ONLY __SCHEMA__.seasonality_profile
    ADD CONSTRAINT uq_seasonality_profile_code UNIQUE (code);

ALTER TABLE ONLY __SCHEMA__.version
    ADD CONSTRAINT uq_version_code UNIQUE (code);

CREATE INDEX ix_capex_item_version ON __SCHEMA__.capex_item USING btree (version_id);

CREATE INDEX ix_closure_descendant ON __SCHEMA__.closure USING btree (hier_id, descendant_id);

CREATE INDEX ix_fact_cost_center_cc ON __SCHEMA__.fact_cost_center USING btree (version_id, dim_cost_center, period);

CREATE INDEX ix_item_list_id ON __SCHEMA__.item USING btree (list_id);

CREATE INDEX ix_note_thread_capex_item ON __SCHEMA__.note_thread USING btree (capex_item_id);

CREATE INDEX ix_note_thread_id ON __SCHEMA__.note USING btree (thread_id);

CREATE INDEX ix_workflow_event_cc ON __SCHEMA__.workflow_event USING btree (version_id, cost_center_id);

CREATE INDEX login_event_address_at_idx ON __SCHEMA__.login_event USING btree (address, at);

CREATE INDEX login_event_login_at_idx ON __SCHEMA__.login_event USING btree (login, at);

CREATE INDEX login_event_user_id_idx ON __SCHEMA__.login_event USING btree (user_id);

CREATE UNIQUE INDEX note_grid_line ON __SCHEMA__.note_grid USING btree (cost_center_id, account_id, work_center_id, activity_type_id) NULLS NOT DISTINCT;

CREATE INDEX note_thread_line ON __SCHEMA__.note_thread USING btree (cost_center_id, account_id, work_center_id, activity_type_id);

CREATE INDEX oidc_login_test_by_idx ON __SCHEMA__.oidc_login USING btree (test_by);

CREATE UNIQUE INDEX uq_workforce_position_version_id_code ON __SCHEMA__.workforce_position USING btree (version_id, code) WHERE (transferred_to_id IS NULL);

CREATE UNIQUE INDEX uq_workforce_transfer_proposed ON __SCHEMA__.workforce_transfer USING btree (position_id) WHERE (status = 'proposed'::text);

CREATE TRIGGER audit_delete AFTER DELETE ON __SCHEMA__.fact_capacity REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.audit_fact_capacity();

CREATE TRIGGER audit_delete AFTER DELETE ON __SCHEMA__.fact_cost_center REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.audit_fact_cost_center();

CREATE TRIGGER audit_delete AFTER DELETE ON __SCHEMA__.fact_workforce REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.audit_fact_workforce();

CREATE TRIGGER audit_insert AFTER INSERT ON __SCHEMA__.fact_capacity REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.audit_fact_capacity();

CREATE TRIGGER audit_insert AFTER INSERT ON __SCHEMA__.fact_cost_center REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.audit_fact_cost_center();

CREATE TRIGGER audit_insert AFTER INSERT ON __SCHEMA__.fact_workforce REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.audit_fact_workforce();

CREATE TRIGGER audit_update AFTER UPDATE ON __SCHEMA__.fact_capacity REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.audit_fact_capacity();

CREATE TRIGGER audit_update AFTER UPDATE ON __SCHEMA__.fact_cost_center REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.audit_fact_cost_center();

CREATE TRIGGER audit_update AFTER UPDATE ON __SCHEMA__.fact_workforce REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.audit_fact_workforce();

CREATE CONSTRAINT TRIGGER cash_out AFTER INSERT OR UPDATE ON __SCHEMA__.capex_cash_out DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.check_capex_cash_out();

CREATE CONSTRAINT TRIGGER cash_out AFTER UPDATE OF amount, kind ON __SCHEMA__.capex_item DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.check_capex_cash_out();

CREATE TRIGGER closed_insert AFTER INSERT ON __SCHEMA__.fact_cost_center REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.lock_closed_months();

CREATE TRIGGER closed_insert AFTER INSERT ON __SCHEMA__.fact_workforce REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.lock_closed_months();

CREATE TRIGGER closed_update AFTER UPDATE ON __SCHEMA__.fact_cost_center REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.lock_closed_months();

CREATE TRIGGER closed_update AFTER UPDATE ON __SCHEMA__.fact_workforce REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.lock_closed_months();

CREATE TRIGGER guard AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.forwarding FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_forwarding();

CREATE TRIGGER guard AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.forwarding_receiver FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_forwarding();

CREATE TRIGGER guard BEFORE UPDATE ON __SCHEMA__.item_list FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_item_list();

CREATE TRIGGER guard AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.pay_owner_seen FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_pay_rights();

CREATE TRIGGER guard AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.pay_reader FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_pay_rights();

CREATE TRIGGER guard AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.pay_release FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_pay_rights();

CREATE TRIGGER guard AFTER INSERT OR UPDATE ON __SCHEMA__.pay_review FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_pay_review();

CREATE TRIGGER guard AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.pay_rule FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_pay_rules();

CREATE TRIGGER guard AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.pay_rule_step FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_pay_rules();

CREATE TRIGGER guard AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.pay_setting FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_pay_rules();

CREATE TRIGGER guard AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.pay_staff_type FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_pay_rules();

CREATE TRIGGER guard BEFORE DELETE OR UPDATE ON __SCHEMA__.workforce_position FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_workforce_position();

CREATE TRIGGER lock AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.capacity_line FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.lock_capacity_line();

CREATE TRIGGER lock AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.capacity_position FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.lock_capacity_staff();

CREATE TRIGGER lock AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.capacity_unit FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.lock_capacity_staff();

CREATE TRIGGER lock AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.capex_cash_out FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.lock_capex();

CREATE TRIGGER lock AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.capex_item FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.lock_capex();

CREATE TRIGGER lock AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.line_spread FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.lock_line_spread();

CREATE TRIGGER lock AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.position_pay FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.lock_position_pay();

CREATE TRIGGER lock AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.workforce_line FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.lock_workforce_row();

CREATE TRIGGER lock AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.workforce_position FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.lock_workforce_row();

CREATE TRIGGER lock_delete AFTER DELETE ON __SCHEMA__.fact_capacity REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.lock_fact_capacity();

CREATE TRIGGER lock_delete AFTER DELETE ON __SCHEMA__.fact_cost_center REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.lock_fact_cost_center();

CREATE TRIGGER lock_delete AFTER DELETE ON __SCHEMA__.fact_workforce REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.lock_fact_workforce();

CREATE TRIGGER lock_insert AFTER INSERT ON __SCHEMA__.fact_capacity REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.lock_fact_capacity();

CREATE TRIGGER lock_insert AFTER INSERT ON __SCHEMA__.fact_cost_center REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.lock_fact_cost_center();

CREATE TRIGGER lock_insert AFTER INSERT ON __SCHEMA__.fact_workforce REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.lock_fact_workforce();

CREATE TRIGGER lock_update AFTER UPDATE ON __SCHEMA__.fact_capacity REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.lock_fact_capacity();

CREATE TRIGGER lock_update AFTER UPDATE ON __SCHEMA__.fact_cost_center REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.lock_fact_cost_center();

CREATE TRIGGER lock_update AFTER UPDATE ON __SCHEMA__.fact_workforce REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION __SCHEMA__.lock_fact_workforce();

CREATE TRIGGER log AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.capacity_line FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_capacity_line();

CREATE TRIGGER log AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.capacity_position FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_capacity_staff();

CREATE TRIGGER log AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.capacity_unit FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_capacity_staff();

CREATE TRIGGER log AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.capex_cash_out FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_capex();

CREATE TRIGGER log AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.capex_item FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_capex();

CREATE TRIGGER log AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.item FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_annotation();

CREATE TRIGGER log AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.item_list FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_annotation();

CREATE TRIGGER log AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.note FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_annotation();

CREATE TRIGGER log AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.note_grid FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_annotation();

CREATE TRIGGER log AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.note_mention FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_annotation();

CREATE TRIGGER log AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.note_thread FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_annotation();

CREATE TRIGGER log AFTER INSERT OR DELETE ON __SCHEMA__.pay_handover FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_pay_handover();

CREATE TRIGGER log AFTER INSERT OR DELETE ON __SCHEMA__.pay_reader FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_pay_reader();

CREATE TRIGGER log AFTER INSERT OR DELETE ON __SCHEMA__.pay_release FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_pay_release();

CREATE TRIGGER log AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.position_pay FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_position_pay();

CREATE TRIGGER log AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.workforce_position FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_workforce_position();

CREATE TRIGGER log_hr AFTER INSERT OR UPDATE OF is_hr ON __SCHEMA__.app_user FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.log_hr_role();

CREATE TRIGGER reopen AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.capex_item FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.capex_reopen_project();

CREATE TRIGGER rules BEFORE INSERT OR UPDATE ON __SCHEMA__.capex_item FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.capex_item_rules();

CREATE TRIGGER step AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.pay_handover FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_pay_step();

CREATE TRIGGER step AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.pay_release FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_pay_step();

CREATE TRIGGER step AFTER INSERT OR DELETE OR UPDATE ON __SCHEMA__.pay_review FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.guard_pay_step();

ALTER TABLE ONLY __SCHEMA__.account_set_account
    ADD CONSTRAINT fk_account_set_account_account_id_member FOREIGN KEY (account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.account_set_account
    ADD CONSTRAINT fk_account_set_account_set_id_account_set FOREIGN KEY (set_id) REFERENCES __SCHEMA__.account_set(set_id) ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.account_set_assignment
    ADD CONSTRAINT fk_account_set_assignment_member_id_member FOREIGN KEY (member_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.account_set_assignment
    ADD CONSTRAINT fk_account_set_assignment_set_id_account_set FOREIGN KEY (set_id) REFERENCES __SCHEMA__.account_set(set_id) ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.account_set_assignment
    ADD CONSTRAINT fk_account_set_assignment_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.account_set
    ADD CONSTRAINT fk_account_set_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.approver_assignment
    ADD CONSTRAINT fk_approver_assignment_member_id_member FOREIGN KEY (member_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.approver_assignment
    ADD CONSTRAINT fk_approver_assignment_user_id_app_user FOREIGN KEY (user_id) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.capacity_grant
    ADD CONSTRAINT fk_capacity_grant_member_id_member FOREIGN KEY (member_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.capacity_grant
    ADD CONSTRAINT fk_capacity_grant_user_id_app_user FOREIGN KEY (user_id) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.capacity_line
    ADD CONSTRAINT fk_capacity_line_activity_type_id_member FOREIGN KEY (activity_type_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.capacity_line
    ADD CONSTRAINT fk_capacity_line_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.capacity_line
    ADD CONSTRAINT fk_capacity_line_profile_id_seasonality_profile FOREIGN KEY (profile_id) REFERENCES __SCHEMA__.seasonality_profile(profile_id);

ALTER TABLE ONLY __SCHEMA__.capacity_line
    ADD CONSTRAINT fk_capacity_line_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.capacity_line
    ADD CONSTRAINT fk_capacity_line_work_center_id_member FOREIGN KEY (work_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.capacity_position
    ADD CONSTRAINT fk_capacity_position_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.capacity_position
    ADD CONSTRAINT fk_capacity_position_position_id_workforce_position FOREIGN KEY (position_id) REFERENCES __SCHEMA__.workforce_position(position_id) ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.capacity_position
    ADD CONSTRAINT fk_capacity_position_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.capacity_position
    ADD CONSTRAINT fk_capacity_position_version_id_work_center_id_activity_5c6e FOREIGN KEY (version_id, work_center_id, activity_type_id) REFERENCES __SCHEMA__.capacity_line(version_id, work_center_id, activity_type_id) ON DELETE SET NULL (work_center_id, activity_type_id);

ALTER TABLE ONLY __SCHEMA__.capacity_unit
    ADD CONSTRAINT fk_capacity_unit_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.capacity_unit
    ADD CONSTRAINT fk_capacity_unit_version_id_default_work_center_id_defa_b04c FOREIGN KEY (version_id, default_work_center_id, default_activity_type_id) REFERENCES __SCHEMA__.capacity_line(version_id, work_center_id, activity_type_id) ON DELETE SET NULL (default_work_center_id, default_activity_type_id);

ALTER TABLE ONLY __SCHEMA__.capacity_unit
    ADD CONSTRAINT fk_capacity_unit_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.capex_cash_out
    ADD CONSTRAINT fk_capex_cash_out_item_id_capex_item FOREIGN KEY (item_id) REFERENCES __SCHEMA__.capex_item(item_id) ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.capex_item
    ADD CONSTRAINT fk_capex_item_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.capex_item
    ADD CONSTRAINT fk_capex_item_project FOREIGN KEY (project_id, cost_center_id) REFERENCES __SCHEMA__.capex_item(item_id, cost_center_id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.capex_item
    ADD CONSTRAINT fk_capex_item_type_id_capex_type FOREIGN KEY (type_id) REFERENCES __SCHEMA__.capex_type(type_id);

ALTER TABLE ONLY __SCHEMA__.capex_item
    ADD CONSTRAINT fk_capex_item_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.capex_setting
    ADD CONSTRAINT fk_capex_setting_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.comparison
    ADD CONSTRAINT fk_comparison_compare_version_id_version FOREIGN KEY (compare_version_id) REFERENCES __SCHEMA__.version(version_id);

ALTER TABLE ONLY __SCHEMA__.comparison
    ADD CONSTRAINT fk_comparison_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.edge_override
    ADD CONSTRAINT fk_edge_override_hier_id_hierarchy FOREIGN KEY (hier_id) REFERENCES __SCHEMA__.hierarchy(hier_id);

ALTER TABLE ONLY __SCHEMA__.edge_override
    ADD CONSTRAINT fk_edge_override_member_id_member FOREIGN KEY (member_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.edge_override
    ADD CONSTRAINT fk_edge_override_parent_id_member FOREIGN KEY (parent_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.entry_form
    ADD CONSTRAINT fk_entry_form_account_id_member FOREIGN KEY (account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.entry_form
    ADD CONSTRAINT fk_entry_form_profile_id_seasonality_profile FOREIGN KEY (profile_id) REFERENCES __SCHEMA__.seasonality_profile(profile_id);

ALTER TABLE ONLY __SCHEMA__.entry_form
    ADD CONSTRAINT fk_entry_form_reference_version_id_version FOREIGN KEY (reference_version_id) REFERENCES __SCHEMA__.version(version_id);

ALTER TABLE ONLY __SCHEMA__.entry_form
    ADD CONSTRAINT fk_entry_form_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.fact_capacity
    ADD CONSTRAINT fk_fact_capacity_dim_activity_type_member FOREIGN KEY (dim_activity_type) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.fact_capacity
    ADD CONSTRAINT fk_fact_capacity_dim_cost_center_member FOREIGN KEY (dim_cost_center) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.fact_capacity
    ADD CONSTRAINT fk_fact_capacity_dim_entity_member FOREIGN KEY (dim_entity) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.fact_capacity
    ADD CONSTRAINT fk_fact_capacity_dim_work_center_member FOREIGN KEY (dim_work_center) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.fact_capacity
    ADD CONSTRAINT fk_fact_capacity_version_id_version FOREIGN KEY (version_id) REFERENCES __SCHEMA__.version(version_id);

ALTER TABLE ONLY __SCHEMA__.fact_cost_center
    ADD CONSTRAINT fk_fact_cost_center_dim_account_member FOREIGN KEY (dim_account) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.fact_cost_center
    ADD CONSTRAINT fk_fact_cost_center_dim_cost_center_member FOREIGN KEY (dim_cost_center) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.fact_cost_center
    ADD CONSTRAINT fk_fact_cost_center_dim_entity_member FOREIGN KEY (dim_entity) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.fact_cost_center
    ADD CONSTRAINT fk_fact_cost_center_version_id_version FOREIGN KEY (version_id) REFERENCES __SCHEMA__.version(version_id);

ALTER TABLE ONLY __SCHEMA__.fact_workforce
    ADD CONSTRAINT fk_fact_workforce_dim_cost_center_member FOREIGN KEY (dim_cost_center) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.fact_workforce
    ADD CONSTRAINT fk_fact_workforce_dim_entity_member FOREIGN KEY (dim_entity) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.fact_workforce
    ADD CONSTRAINT fk_fact_workforce_dim_staff_category_member FOREIGN KEY (dim_staff_category) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.fact_workforce
    ADD CONSTRAINT fk_fact_workforce_dim_staff_type_member FOREIGN KEY (dim_staff_type) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.fact_workforce
    ADD CONSTRAINT fk_fact_workforce_version_id_version FOREIGN KEY (version_id) REFERENCES __SCHEMA__.version(version_id);

ALTER TABLE ONLY __SCHEMA__.forwarding
    ADD CONSTRAINT fk_forwarding_charge_account_id_member FOREIGN KEY (charge_account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.forwarding
    ADD CONSTRAINT fk_forwarding_credit_account_id_member FOREIGN KEY (credit_account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.forwarding_receiver
    ADD CONSTRAINT fk_forwarding_receiver_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.forwarding_receiver
    ADD CONSTRAINT fk_forwarding_receiver_forwarding_id_forwarding FOREIGN KEY (forwarding_id) REFERENCES __SCHEMA__.forwarding(forwarding_id) ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.forwarding
    ADD CONSTRAINT fk_forwarding_source_id_member FOREIGN KEY (source_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.forwarding
    ADD CONSTRAINT fk_forwarding_version_id_planning_round FOREIGN KEY (version_id) REFERENCES __SCHEMA__.planning_round(version_id);

ALTER TABLE ONLY __SCHEMA__.not_planned_account
    ADD CONSTRAINT fk_hidden_account_account_id_member FOREIGN KEY (account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.not_planned_account
    ADD CONSTRAINT fk_hidden_account_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.hier_edge
    ADD CONSTRAINT fk_hier_edge_hier_id_hierarchy FOREIGN KEY (hier_id) REFERENCES __SCHEMA__.hierarchy(hier_id);

ALTER TABLE ONLY __SCHEMA__.hier_edge
    ADD CONSTRAINT fk_hier_edge_member_id_member FOREIGN KEY (member_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.hier_edge
    ADD CONSTRAINT fk_hier_edge_parent_id_member FOREIGN KEY (parent_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.hierarchy
    ADD CONSTRAINT fk_hierarchy_dim_id_dimension FOREIGN KEY (dim_id) REFERENCES __SCHEMA__.dimension(dim_id);

ALTER TABLE ONLY __SCHEMA__.hr_pay
    ADD CONSTRAINT fk_hr_pay_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.hr_staff
    ADD CONSTRAINT fk_hr_staff_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.hr_staff
    ADD CONSTRAINT fk_hr_staff_staff_category_id_member FOREIGN KEY (staff_category_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.hr_staff
    ADD CONSTRAINT fk_hr_staff_staff_type_id_member FOREIGN KEY (staff_type_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.item_list
    ADD CONSTRAINT fk_item_list_account_id_member FOREIGN KEY (account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.item_list
    ADD CONSTRAINT fk_item_list_changed_by_app_user FOREIGN KEY (changed_by) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.item_list
    ADD CONSTRAINT fk_item_list_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.item
    ADD CONSTRAINT fk_item_list_id_item_list FOREIGN KEY (list_id) REFERENCES __SCHEMA__.item_list(list_id) ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.item_list
    ADD CONSTRAINT fk_item_list_owner_id_app_user FOREIGN KEY (owner_id) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.line_spread
    ADD CONSTRAINT fk_line_spread_account_id_member FOREIGN KEY (account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.line_spread
    ADD CONSTRAINT fk_line_spread_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.line_spread
    ADD CONSTRAINT fk_line_spread_profile_id_seasonality_profile FOREIGN KEY (profile_id) REFERENCES __SCHEMA__.seasonality_profile(profile_id);

ALTER TABLE ONLY __SCHEMA__.line_spread
    ADD CONSTRAINT fk_line_spread_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.member
    ADD CONSTRAINT fk_member_dim_id_dimension FOREIGN KEY (dim_id) REFERENCES __SCHEMA__.dimension(dim_id);

ALTER TABLE ONLY __SCHEMA__.member_override
    ADD CONSTRAINT fk_member_override_member_id_member FOREIGN KEY (member_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.member_shadow
    ADD CONSTRAINT fk_member_shadow_member_id_member FOREIGN KEY (member_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.note
    ADD CONSTRAINT fk_note_author_id_app_user FOREIGN KEY (author_id) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.note_grid
    ADD CONSTRAINT fk_note_grid_account_id_member FOREIGN KEY (account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.note_grid
    ADD CONSTRAINT fk_note_grid_activity_type_id_member FOREIGN KEY (activity_type_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.note_grid
    ADD CONSTRAINT fk_note_grid_changed_by_app_user FOREIGN KEY (changed_by) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.note_grid
    ADD CONSTRAINT fk_note_grid_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.note_grid
    ADD CONSTRAINT fk_note_grid_round_id_planning_round FOREIGN KEY (round_id) REFERENCES __SCHEMA__.planning_round(version_id);

ALTER TABLE ONLY __SCHEMA__.note_grid
    ADD CONSTRAINT fk_note_grid_work_center_id_member FOREIGN KEY (work_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.note_mention
    ADD CONSTRAINT fk_note_mention_note_id_note FOREIGN KEY (note_id) REFERENCES __SCHEMA__.note(note_id) ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.note_mention
    ADD CONSTRAINT fk_note_mention_user_id_app_user FOREIGN KEY (user_id) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.note_thread
    ADD CONSTRAINT fk_note_thread_account_id_member FOREIGN KEY (account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.note_thread
    ADD CONSTRAINT fk_note_thread_activity_type_id_member FOREIGN KEY (activity_type_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.note_thread
    ADD CONSTRAINT fk_note_thread_assignee_id_app_user FOREIGN KEY (assignee_id) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.note_thread
    ADD CONSTRAINT fk_note_thread_capex_item_id_capex_item FOREIGN KEY (capex_item_id) REFERENCES __SCHEMA__.capex_item(item_id) ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.note_thread
    ADD CONSTRAINT fk_note_thread_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.note_thread
    ADD CONSTRAINT fk_note_thread_created_by_app_user FOREIGN KEY (created_by) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.note
    ADD CONSTRAINT fk_note_thread_id_note_thread FOREIGN KEY (thread_id) REFERENCES __SCHEMA__.note_thread(thread_id) ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.note_thread
    ADD CONSTRAINT fk_note_thread_resolved_by_app_user FOREIGN KEY (resolved_by) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.note_thread
    ADD CONSTRAINT fk_note_thread_round_id_planning_round FOREIGN KEY (round_id) REFERENCES __SCHEMA__.planning_round(version_id);

ALTER TABLE ONLY __SCHEMA__.note_thread
    ADD CONSTRAINT fk_note_thread_work_center_id_member FOREIGN KEY (work_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.owner_override
    ADD CONSTRAINT fk_owner_override_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.owner_override
    ADD CONSTRAINT fk_owner_override_user_id_app_user FOREIGN KEY (user_id) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.pay_handover
    ADD CONSTRAINT fk_pay_handover_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.pay_handover
    ADD CONSTRAINT fk_pay_handover_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.pay_owner_seen
    ADD CONSTRAINT fk_pay_owner_seen_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.pay_owner_seen
    ADD CONSTRAINT fk_pay_owner_seen_owner_id_app_user FOREIGN KEY (owner_id) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.pay_owner_seen
    ADD CONSTRAINT fk_pay_owner_seen_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.pay_reader
    ADD CONSTRAINT fk_pay_reader_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.pay_reader
    ADD CONSTRAINT fk_pay_reader_user_id_app_user FOREIGN KEY (user_id) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.pay_reader
    ADD CONSTRAINT fk_pay_reader_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.pay_release
    ADD CONSTRAINT fk_pay_release_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.pay_review
    ADD CONSTRAINT fk_pay_review_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.pay_rule
    ADD CONSTRAINT fk_pay_rule_account_id_member FOREIGN KEY (account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.pay_rule
    ADD CONSTRAINT fk_pay_rule_entity_id_member FOREIGN KEY (entity_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.pay_rule_step
    ADD CONSTRAINT fk_pay_rule_step_rule_id_pay_rule FOREIGN KEY (rule_id) REFERENCES __SCHEMA__.pay_rule(rule_id) ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.pay_rule
    ADD CONSTRAINT fk_pay_rule_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.pay_setting
    ADD CONSTRAINT fk_pay_setting_bonus_account_id_member FOREIGN KEY (bonus_account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.pay_setting
    ADD CONSTRAINT fk_pay_setting_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.pay_small_group
    ADD CONSTRAINT fk_pay_small_group_account_id_member FOREIGN KEY (account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.pay_small_group
    ADD CONSTRAINT fk_pay_small_group_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.pay_staff_type
    ADD CONSTRAINT fk_pay_staff_type_account_id_member FOREIGN KEY (account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.pay_staff_type
    ADD CONSTRAINT fk_pay_staff_type_staff_type_id_member FOREIGN KEY (staff_type_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.pay_staff_type
    ADD CONSTRAINT fk_pay_staff_type_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.planner_grant
    ADD CONSTRAINT fk_planner_grant_member_id_member FOREIGN KEY (member_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.planner_grant
    ADD CONSTRAINT fk_planner_grant_user_id_app_user FOREIGN KEY (user_id) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.planning_round
    ADD CONSTRAINT fk_planning_round_copied_from_planning_round FOREIGN KEY (copied_from) REFERENCES __SCHEMA__.planning_round(version_id);

ALTER TABLE ONLY __SCHEMA__.planning_round
    ADD CONSTRAINT fk_planning_round_version_id_version FOREIGN KEY (version_id) REFERENCES __SCHEMA__.version(version_id);

ALTER TABLE ONLY __SCHEMA__.position_pay
    ADD CONSTRAINT fk_position_pay_charged_to_id_member FOREIGN KEY (charged_to_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.position_pay
    ADD CONSTRAINT fk_position_pay_position_id_workforce_position FOREIGN KEY (position_id) REFERENCES __SCHEMA__.workforce_position(position_id) ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.position_pay
    ADD CONSTRAINT fk_position_pay_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.publish_run
    ADD CONSTRAINT fk_publish_run_version_id_version FOREIGN KEY (version_id) REFERENCES __SCHEMA__.version(version_id);

ALTER TABLE ONLY __SCHEMA__.round_member
    ADD CONSTRAINT fk_round_member_member_id_member FOREIGN KEY (member_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.round_member
    ADD CONSTRAINT fk_round_member_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.round_module
    ADD CONSTRAINT fk_round_module_version_id_planning_round FOREIGN KEY (version_id) REFERENCES __SCHEMA__.planning_round(version_id);

ALTER TABLE ONLY __SCHEMA__.sheet_account
    ADD CONSTRAINT fk_sheet_account_account_id_member FOREIGN KEY (account_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.sheet_account
    ADD CONSTRAINT fk_sheet_account_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.sheet_account
    ADD CONSTRAINT fk_sheet_account_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.user_session
    ADD CONSTRAINT fk_user_session_user_id_app_user FOREIGN KEY (user_id) REFERENCES __SCHEMA__.app_user(user_id) ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.work_center_rate
    ADD CONSTRAINT fk_work_center_rate_activity_type_id_member FOREIGN KEY (activity_type_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.work_center_rate
    ADD CONSTRAINT fk_work_center_rate_work_center_id_member FOREIGN KEY (work_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.workflow_event
    ADD CONSTRAINT fk_workflow_event_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.workflow_event
    ADD CONSTRAINT fk_workflow_event_version_id_planning_round FOREIGN KEY (version_id) REFERENCES __SCHEMA__.planning_round(version_id);

ALTER TABLE ONLY __SCHEMA__.workflow_status
    ADD CONSTRAINT fk_workflow_status_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.workflow_status
    ADD CONSTRAINT fk_workflow_status_version_id_planning_round FOREIGN KEY (version_id) REFERENCES __SCHEMA__.planning_round(version_id);

ALTER TABLE ONLY __SCHEMA__.workforce_line
    ADD CONSTRAINT fk_workforce_line_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.workforce_line
    ADD CONSTRAINT fk_workforce_line_staff_category_id_member FOREIGN KEY (staff_category_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.workforce_line
    ADD CONSTRAINT fk_workforce_line_staff_type_id_member FOREIGN KEY (staff_type_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.workforce_line
    ADD CONSTRAINT fk_workforce_line_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.workforce_position
    ADD CONSTRAINT fk_workforce_position_cost_center_id_member FOREIGN KEY (cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.workforce_position
    ADD CONSTRAINT fk_workforce_position_staff_category_id_member FOREIGN KEY (staff_category_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.workforce_position
    ADD CONSTRAINT fk_workforce_position_staff_type_id_member FOREIGN KEY (staff_type_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.workforce_position
    ADD CONSTRAINT fk_workforce_position_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.workforce_transfer
    ADD CONSTRAINT fk_workforce_transfer_decided_by_app_user FOREIGN KEY (decided_by) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.workforce_transfer
    ADD CONSTRAINT fk_workforce_transfer_from_cost_center_id_member FOREIGN KEY (from_cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.workforce_transfer
    ADD CONSTRAINT fk_workforce_transfer_new_position_id_workforce_position FOREIGN KEY (new_position_id) REFERENCES __SCHEMA__.workforce_position(position_id) ON DELETE SET NULL;

ALTER TABLE ONLY __SCHEMA__.workforce_transfer
    ADD CONSTRAINT fk_workforce_transfer_position_id_workforce_position FOREIGN KEY (position_id) REFERENCES __SCHEMA__.workforce_position(position_id) ON DELETE CASCADE;

ALTER TABLE ONLY __SCHEMA__.workforce_transfer
    ADD CONSTRAINT fk_workforce_transfer_proposed_by_app_user FOREIGN KEY (proposed_by) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.workforce_transfer
    ADD CONSTRAINT fk_workforce_transfer_to_cost_center_id_member FOREIGN KEY (to_cost_center_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.workforce_transfer
    ADD CONSTRAINT fk_workforce_transfer_version_id_module_round_module FOREIGN KEY (version_id, module) REFERENCES __SCHEMA__.round_module(version_id, module);

ALTER TABLE ONLY __SCHEMA__.hier_edge
    ADD CONSTRAINT hier_edge_source_parent_id_fkey FOREIGN KEY (source_parent_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.login_event
    ADD CONSTRAINT login_event_user_id_fkey FOREIGN KEY (user_id) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.oidc_login
    ADD CONSTRAINT oidc_login_test_by_fkey FOREIGN KEY (test_by) REFERENCES __SCHEMA__.app_user(user_id);

ALTER TABLE ONLY __SCHEMA__.workforce_position
    ADD CONSTRAINT workforce_position_transferred_from_id_fkey FOREIGN KEY (transferred_from_id) REFERENCES __SCHEMA__.member(member_id);

ALTER TABLE ONLY __SCHEMA__.workforce_position
    ADD CONSTRAINT workforce_position_transferred_to_id_fkey FOREIGN KEY (transferred_to_id) REFERENCES __SCHEMA__.member(member_id);

CREATE POLICY add ON __SCHEMA__.capacity_line FOR INSERT WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))));

CREATE POLICY add ON __SCHEMA__.capacity_position FOR INSERT WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))));

CREATE POLICY add ON __SCHEMA__.capacity_unit FOR INSERT WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))));

CREATE POLICY add ON __SCHEMA__.capex_cash_out FOR INSERT WITH CHECK ((item_id IN ( SELECT capex_item.item_id
   FROM __SCHEMA__.capex_item
  WHERE (capex_item.cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
           FROM __SCHEMA__.cost_center_access
          WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))))));

CREATE POLICY add ON __SCHEMA__.capex_item FOR INSERT WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY add ON __SCHEMA__.fact_capacity FOR INSERT WITH CHECK ((dim_cost_center = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))));

CREATE POLICY add ON __SCHEMA__.fact_cost_center FOR INSERT WITH CHECK (((dim_cost_center = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))) OR ((dim_account = ANY (ARRAY( SELECT credit_account.account_id
   FROM __SCHEMA__.credit_account))) AND (dim_cost_center = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))))));

CREATE POLICY add ON __SCHEMA__.fact_workforce FOR INSERT WITH CHECK ((dim_cost_center = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY add ON __SCHEMA__.hr_pay FOR INSERT WITH CHECK ((EXISTS ( SELECT 1
   FROM __SCHEMA__.app_user u
  WHERE ((u.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND (u.is_hr OR u.is_system) AND (NOT u.disabled)))));

CREATE POLICY add ON __SCHEMA__.hr_staff FOR INSERT WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY add ON __SCHEMA__.item FOR INSERT WITH CHECK ((list_id IN ( SELECT item_list.list_id
   FROM __SCHEMA__.item_list
  WHERE ((item_list.owner_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) OR ((item_list.visibility = ANY (ARRAY['cost_center'::text, 'public'::text])) AND (item_list.cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
           FROM __SCHEMA__.cost_center_access
          WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))))))));

CREATE POLICY add ON __SCHEMA__.item_list FOR INSERT WITH CHECK (((owner_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))));

CREATE POLICY add ON __SCHEMA__.line_spread FOR INSERT WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY add ON __SCHEMA__.note FOR INSERT WITH CHECK (((thread_id IN ( SELECT note_thread.thread_id
   FROM __SCHEMA__.note_thread)) AND (author_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)));

CREATE POLICY add ON __SCHEMA__.note_grid FOR INSERT WITH CHECK ((((module = 'cost_center'::text) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))) OR ((module = 'capacity'::text) AND (cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))))));

CREATE POLICY add ON __SCHEMA__.note_mention FOR INSERT WITH CHECK ((note_id IN ( SELECT note.note_id
   FROM __SCHEMA__.note
  WHERE (note.author_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))));

CREATE POLICY add ON __SCHEMA__.note_thread FOR INSERT WITH CHECK ((((module = 'cost_center'::text) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))) OR ((module = 'capacity'::text) AND (cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE (capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))) OR ((module = 'capex'::text) AND (capex_item_id IN ( SELECT capex_item.item_id
   FROM __SCHEMA__.capex_item)))));

CREATE POLICY add ON __SCHEMA__.position_pay FOR INSERT WITH CHECK ((position_id IN ( SELECT p.position_id
   FROM __SCHEMA__.workforce_position p
  WHERE ((p.version_id, p.cost_center_id) IN ( SELECT pay_access.version_id,
            pay_access.cost_center_id
           FROM __SCHEMA__.pay_access
          WHERE (pay_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))));

CREATE POLICY add ON __SCHEMA__.position_pay_log FOR INSERT WITH CHECK (true);

CREATE POLICY add ON __SCHEMA__.workforce_line FOR INSERT WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY add ON __SCHEMA__.workforce_position FOR INSERT WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY add ON __SCHEMA__.workforce_transfer FOR INSERT WITH CHECK ((from_cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

ALTER TABLE __SCHEMA__.capacity_line ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.capacity_position ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.capacity_unit ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.capex_cash_out ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.capex_item ENABLE ROW LEVEL SECURITY;

CREATE POLICY change ON __SCHEMA__.capacity_line FOR UPDATE USING ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write))))) WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))));

CREATE POLICY change ON __SCHEMA__.capacity_position FOR UPDATE USING ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write))))) WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))));

CREATE POLICY change ON __SCHEMA__.capacity_unit FOR UPDATE USING ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write))))) WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))));

CREATE POLICY change ON __SCHEMA__.capex_cash_out FOR UPDATE USING ((item_id IN ( SELECT capex_item.item_id
   FROM __SCHEMA__.capex_item
  WHERE (capex_item.cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
           FROM __SCHEMA__.cost_center_access
          WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))))) WITH CHECK ((item_id IN ( SELECT capex_item.item_id
   FROM __SCHEMA__.capex_item
  WHERE (capex_item.cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
           FROM __SCHEMA__.cost_center_access
          WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))))));

CREATE POLICY change ON __SCHEMA__.capex_item FOR UPDATE USING (((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))) OR (cost_center_id = ANY (ARRAY( SELECT capex_confirmer.cost_center_id
   FROM __SCHEMA__.capex_confirmer
  WHERE (capex_confirmer.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))))) WITH CHECK (((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))) OR (cost_center_id = ANY (ARRAY( SELECT capex_confirmer.cost_center_id
   FROM __SCHEMA__.capex_confirmer
  WHERE (capex_confirmer.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))));

CREATE POLICY change ON __SCHEMA__.fact_capacity FOR UPDATE USING ((dim_cost_center = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write))))) WITH CHECK ((dim_cost_center = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))));

CREATE POLICY change ON __SCHEMA__.fact_cost_center FOR UPDATE USING (((dim_cost_center = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))) OR ((dim_account = ANY (ARRAY( SELECT credit_account.account_id
   FROM __SCHEMA__.credit_account))) AND (dim_cost_center = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write))))))) WITH CHECK (((dim_cost_center = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))) OR ((dim_account = ANY (ARRAY( SELECT credit_account.account_id
   FROM __SCHEMA__.credit_account))) AND (dim_cost_center = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))))));

CREATE POLICY change ON __SCHEMA__.fact_workforce FOR UPDATE USING ((dim_cost_center = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))) WITH CHECK ((dim_cost_center = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY change ON __SCHEMA__.hr_pay FOR UPDATE USING ((EXISTS ( SELECT 1
   FROM __SCHEMA__.app_user u
  WHERE ((u.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND (u.is_hr OR u.is_system) AND (NOT u.disabled))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM __SCHEMA__.app_user u
  WHERE ((u.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND (u.is_hr OR u.is_system) AND (NOT u.disabled)))));

CREATE POLICY change ON __SCHEMA__.hr_staff FOR UPDATE USING ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))) WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY change ON __SCHEMA__.item FOR UPDATE USING ((list_id IN ( SELECT item_list.list_id
   FROM __SCHEMA__.item_list
  WHERE ((item_list.owner_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) OR ((item_list.visibility = ANY (ARRAY['cost_center'::text, 'public'::text])) AND (item_list.cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
           FROM __SCHEMA__.cost_center_access
          WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))))))) WITH CHECK ((list_id IN ( SELECT item_list.list_id
   FROM __SCHEMA__.item_list
  WHERE ((item_list.owner_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) OR ((item_list.visibility = ANY (ARRAY['cost_center'::text, 'public'::text])) AND (item_list.cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
           FROM __SCHEMA__.cost_center_access
          WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))))))));

CREATE POLICY change ON __SCHEMA__.item_list FOR UPDATE USING (((owner_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) OR ((visibility = ANY (ARRAY['cost_center'::text, 'public'::text])) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))))) WITH CHECK (((owner_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) OR ((visibility = ANY (ARRAY['cost_center'::text, 'public'::text])) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))))));

CREATE POLICY change ON __SCHEMA__.line_spread FOR UPDATE USING ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))) WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY change ON __SCHEMA__.note FOR UPDATE USING (((thread_id IN ( SELECT note_thread.thread_id
   FROM __SCHEMA__.note_thread)) AND (author_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))) WITH CHECK (((thread_id IN ( SELECT note_thread.thread_id
   FROM __SCHEMA__.note_thread)) AND (author_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)));

CREATE POLICY change ON __SCHEMA__.note_grid FOR UPDATE USING ((((module = 'cost_center'::text) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))) OR ((module = 'capacity'::text) AND (cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write))))))) WITH CHECK ((((module = 'cost_center'::text) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))) OR ((module = 'capacity'::text) AND (cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))))));

CREATE POLICY change ON __SCHEMA__.note_mention FOR UPDATE USING ((note_id IN ( SELECT note.note_id
   FROM __SCHEMA__.note
  WHERE (note.author_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))) WITH CHECK ((note_id IN ( SELECT note.note_id
   FROM __SCHEMA__.note
  WHERE (note.author_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))));

CREATE POLICY change ON __SCHEMA__.note_thread FOR UPDATE USING ((((module = 'cost_center'::text) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))) OR ((module = 'capacity'::text) AND (cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE (capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))) OR ((module = 'capex'::text) AND (capex_item_id IN ( SELECT capex_item.item_id
   FROM __SCHEMA__.capex_item))))) WITH CHECK ((((module = 'cost_center'::text) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))) OR ((module = 'capacity'::text) AND (cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE (capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))) OR ((module = 'capex'::text) AND (capex_item_id IN ( SELECT capex_item.item_id
   FROM __SCHEMA__.capex_item)))));

CREATE POLICY change ON __SCHEMA__.position_pay FOR UPDATE USING ((position_id IN ( SELECT p.position_id
   FROM __SCHEMA__.workforce_position p
  WHERE ((p.version_id, p.cost_center_id) IN ( SELECT pay_access.version_id,
            pay_access.cost_center_id
           FROM __SCHEMA__.pay_access
          WHERE (pay_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))))) WITH CHECK ((position_id IN ( SELECT p.position_id
   FROM __SCHEMA__.workforce_position p
  WHERE ((p.version_id, p.cost_center_id) IN ( SELECT pay_access.version_id,
            pay_access.cost_center_id
           FROM __SCHEMA__.pay_access
          WHERE (pay_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))));

CREATE POLICY change ON __SCHEMA__.workforce_line FOR UPDATE USING ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))) WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY change ON __SCHEMA__.workforce_position FOR UPDATE USING ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))) WITH CHECK ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY change ON __SCHEMA__.workforce_transfer FOR UPDATE USING (((from_cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))) OR (to_cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))))) WITH CHECK (((from_cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))) OR (to_cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))));

ALTER TABLE __SCHEMA__.fact_capacity ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.fact_cost_center ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.fact_workforce ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.hr_pay ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.hr_staff ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.item ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.item_list ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.line_spread ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.note ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.note_grid ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.note_mention ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.note_thread ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.position_pay ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.position_pay_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY read ON __SCHEMA__.capacity_line FOR SELECT USING ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE (capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))));

CREATE POLICY read ON __SCHEMA__.capacity_position FOR SELECT USING ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE (capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))));

CREATE POLICY read ON __SCHEMA__.capacity_unit FOR SELECT USING ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE (capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))));

CREATE POLICY read ON __SCHEMA__.capex_cash_out FOR SELECT USING ((item_id IN ( SELECT capex_item.item_id
   FROM __SCHEMA__.capex_item)));

CREATE POLICY read ON __SCHEMA__.capex_item FOR SELECT USING ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))));

CREATE POLICY read ON __SCHEMA__.fact_capacity FOR SELECT USING ((dim_cost_center = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE (capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))));

CREATE POLICY read ON __SCHEMA__.fact_cost_center FOR SELECT USING (((dim_cost_center = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))) AND ((NOT ((version_id, dim_cost_center, dim_account) IN ( SELECT pay_small_group.version_id,
    pay_small_group.cost_center_id,
    pay_small_group.account_id
   FROM __SCHEMA__.pay_small_group))) OR ((version_id, dim_cost_center) IN ( SELECT pay_access.version_id,
    pay_access.cost_center_id
   FROM __SCHEMA__.pay_access
  WHERE (pay_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))));

CREATE POLICY read ON __SCHEMA__.fact_workforce FOR SELECT USING ((dim_cost_center = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))));

CREATE POLICY read ON __SCHEMA__.hr_pay FOR SELECT USING ((EXISTS ( SELECT 1
   FROM __SCHEMA__.app_user u
  WHERE ((u.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND (u.is_hr OR u.is_system) AND (NOT u.disabled)))));

CREATE POLICY read ON __SCHEMA__.hr_staff FOR SELECT USING ((__SCHEMA__.writes_as_hr() OR (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))));

CREATE POLICY read ON __SCHEMA__.item FOR SELECT USING ((list_id IN ( SELECT item_list.list_id
   FROM __SCHEMA__.item_list)));

CREATE POLICY read ON __SCHEMA__.item_list FOR SELECT USING (((owner_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) OR ((visibility = 'cost_center'::text) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))) OR ((visibility = 'public'::text) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))))));

CREATE POLICY read ON __SCHEMA__.line_spread FOR SELECT USING ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))));

CREATE POLICY read ON __SCHEMA__.note FOR SELECT USING ((thread_id IN ( SELECT note_thread.thread_id
   FROM __SCHEMA__.note_thread)));

CREATE POLICY read ON __SCHEMA__.note_grid FOR SELECT USING ((((module = 'cost_center'::text) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))) OR ((module = 'capacity'::text) AND (cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE (capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))))));

CREATE POLICY read ON __SCHEMA__.note_mention FOR SELECT USING ((note_id IN ( SELECT note.note_id
   FROM __SCHEMA__.note)));

CREATE POLICY read ON __SCHEMA__.note_thread FOR SELECT USING ((((module = 'cost_center'::text) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))) OR ((module = 'capacity'::text) AND (cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE (capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))) OR ((module = 'capex'::text) AND (capex_item_id IN ( SELECT capex_item.item_id
   FROM __SCHEMA__.capex_item)))));

CREATE POLICY read ON __SCHEMA__.position_pay FOR SELECT USING ((position_id IN ( SELECT p.position_id
   FROM __SCHEMA__.workforce_position p
  WHERE ((p.version_id, p.cost_center_id) IN ( SELECT pay_access.version_id,
            pay_access.cost_center_id
           FROM __SCHEMA__.pay_access
          WHERE (pay_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))));

CREATE POLICY read ON __SCHEMA__.position_pay_log FOR SELECT USING (((version_id, cost_center_id) IN ( SELECT pay_access.version_id,
    pay_access.cost_center_id
   FROM __SCHEMA__.pay_access
  WHERE (pay_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))));

CREATE POLICY read ON __SCHEMA__.workforce_line FOR SELECT USING ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))));

CREATE POLICY read ON __SCHEMA__.workforce_position FOR SELECT USING ((__SCHEMA__.writes_as_hr() OR (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))));

CREATE POLICY read ON __SCHEMA__.workforce_transfer FOR SELECT USING (((from_cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)))) OR (to_cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))));

CREATE POLICY remove ON __SCHEMA__.capacity_line FOR DELETE USING ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))));

CREATE POLICY remove ON __SCHEMA__.capacity_position FOR DELETE USING ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))));

CREATE POLICY remove ON __SCHEMA__.capacity_unit FOR DELETE USING ((cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))));

CREATE POLICY remove ON __SCHEMA__.capex_cash_out FOR DELETE USING ((item_id IN ( SELECT capex_item.item_id
   FROM __SCHEMA__.capex_item
  WHERE (capex_item.cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
           FROM __SCHEMA__.cost_center_access
          WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))))));

CREATE POLICY remove ON __SCHEMA__.capex_item FOR DELETE USING ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY remove ON __SCHEMA__.fact_capacity FOR DELETE USING ((dim_cost_center = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))));

CREATE POLICY remove ON __SCHEMA__.fact_cost_center FOR DELETE USING (((dim_cost_center = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))) OR ((dim_account = ANY (ARRAY( SELECT credit_account.account_id
   FROM __SCHEMA__.credit_account))) AND (dim_cost_center = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))))));

CREATE POLICY remove ON __SCHEMA__.fact_workforce FOR DELETE USING ((dim_cost_center = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY remove ON __SCHEMA__.hr_pay FOR DELETE USING ((EXISTS ( SELECT 1
   FROM __SCHEMA__.app_user u
  WHERE ((u.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND (u.is_hr OR u.is_system) AND (NOT u.disabled)))));

CREATE POLICY remove ON __SCHEMA__.hr_staff FOR DELETE USING ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY remove ON __SCHEMA__.item FOR DELETE USING ((list_id IN ( SELECT item_list.list_id
   FROM __SCHEMA__.item_list
  WHERE ((item_list.owner_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) OR ((item_list.visibility = ANY (ARRAY['cost_center'::text, 'public'::text])) AND (item_list.cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
           FROM __SCHEMA__.cost_center_access
          WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))))))));

CREATE POLICY remove ON __SCHEMA__.item_list FOR DELETE USING ((owner_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer));

CREATE POLICY remove ON __SCHEMA__.line_spread FOR DELETE USING ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY remove ON __SCHEMA__.note FOR DELETE USING (((thread_id IN ( SELECT note_thread.thread_id
   FROM __SCHEMA__.note_thread)) AND (author_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)));

CREATE POLICY remove ON __SCHEMA__.note_grid FOR DELETE USING ((((module = 'cost_center'::text) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))) OR ((module = 'capacity'::text) AND (cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE ((capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND capacity_access.can_write)))))));

CREATE POLICY remove ON __SCHEMA__.note_mention FOR DELETE USING ((note_id IN ( SELECT note.note_id
   FROM __SCHEMA__.note
  WHERE (note.author_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))));

CREATE POLICY remove ON __SCHEMA__.note_thread FOR DELETE USING (((((module = 'cost_center'::text) AND (cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE (cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))) OR ((module = 'capacity'::text) AND (cost_center_id = ANY (ARRAY( SELECT capacity_access.cost_center_id
   FROM __SCHEMA__.capacity_access
  WHERE (capacity_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))) OR ((module = 'capex'::text) AND (capex_item_id IN ( SELECT capex_item.item_id
   FROM __SCHEMA__.capex_item)))) AND (created_by = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer)));

CREATE POLICY remove ON __SCHEMA__.position_pay FOR DELETE USING ((position_id IN ( SELECT p.position_id
   FROM __SCHEMA__.workforce_position p
  WHERE ((p.version_id, p.cost_center_id) IN ( SELECT pay_access.version_id,
            pay_access.cost_center_id
           FROM __SCHEMA__.pay_access
          WHERE (pay_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer))))));

CREATE POLICY remove ON __SCHEMA__.workforce_line FOR DELETE USING ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY remove ON __SCHEMA__.workforce_position FOR DELETE USING ((cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))));

CREATE POLICY remove ON __SCHEMA__.workforce_transfer FOR DELETE USING (((from_cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write)))) OR (to_cost_center_id = ANY (ARRAY( SELECT cost_center_access.cost_center_id
   FROM __SCHEMA__.cost_center_access
  WHERE ((cost_center_access.user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::integer) AND cost_center_access.can_write))))));

ALTER TABLE __SCHEMA__.workforce_line ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.workforce_position ENABLE ROW LEVEL SECURITY;

ALTER TABLE __SCHEMA__.workforce_transfer ENABLE ROW LEVEL SECURITY;

-- Seed rows

INSERT INTO __SCHEMA__.app_user (user_id, login, email, display_name, is_admin, is_controller, is_viewer, is_system, disabled, created_at, is_hr, number_separators, number_decimals, number_scale, number_units, password_hash, identity) VALUES
	(1, 'system', NULL, 'System', false, true, false, true, false, now(), false, NULL, NULL, NULL, NULL, NULL, NULL);

INSERT INTO __SCHEMA__.dimension (dim_id, code, name) VALUES
	(1, 'entity', 'Entity'),
	(2, 'cost_center', 'Cost center'),
	(3, 'account', 'Account'),
	(4, 'work_center', 'Work center'),
	(5, 'activity_type', 'Activity type'),
	(6, 'staff_type', 'Staff type'),
	(7, 'staff_category', 'Staff category');

INSERT INTO __SCHEMA__.hierarchy (hier_id, dim_id, code, name) VALUES
	(1, 2, 'cost_center', 'Cost centers'),
	(2, 3, 'account', 'Accounts');

INSERT INTO __SCHEMA__.installation (id, fiscal_year_start_month, created_at, published_signs, pay_from_source, number_separators, number_decimals, number_scale, number_units) VALUES
	(1, 1, now(), 'ledger', false, 'comma_dot', false, 'full', true);

INSERT INTO __SCHEMA__.login_setting (id, method, oidc_label, oidc_issuer, oidc_client_id, oidc_scopes, oidc_login_claim, oidc_email_claim, oidc_name_claim, oidc_groups_claim, ldap_url, ldap_bind_dn, ldap_base_dn, ldap_login_attribute, ldap_email_attribute, ldap_name_attribute, ca_pem, allowed_group, admin_group, controller_group, viewer_group, hr_group, setup_code_hash, updated_at, oidc_hosted_domain, oidc_client_secret) VALUES
	(1, 'none', '', '', '', 'openid profile email', 'preferred_username', 'email', 'name', 'groups', '', '', '', 'uid', 'mail', 'cn', '', '', '', '', '', '', NULL, now(), '', NULL);

INSERT INTO __SCHEMA__.member (member_id, dim_id, code, name, is_group, entity_code, owner, account_type, valid_from, valid_to, origin, source_name, cost_center_code, account_code, variable_account_code, missing_since, external) VALUES
	(1, 1, '-', 'No entity', false, NULL, NULL, NULL, NULL, NULL, 'manual', NULL, NULL, NULL, NULL, NULL, NULL),
	(2, 6, 'EMP', 'Employee', false, NULL, NULL, NULL, NULL, NULL, 'manual', NULL, NULL, NULL, NULL, NULL, false),
	(3, 6, 'APP', 'Apprentice', false, NULL, NULL, NULL, NULL, NULL, 'manual', NULL, NULL, NULL, NULL, NULL, false),
	(4, 6, 'TMP', 'Temporary (agency)', false, NULL, NULL, NULL, NULL, NULL, 'manual', NULL, NULL, NULL, NULL, NULL, true),
	(5, 6, 'FRL', 'Freelancer', false, NULL, NULL, NULL, NULL, NULL, 'manual', NULL, NULL, NULL, NULL, NULL, true);

SELECT pg_catalog.setval('__SCHEMA__.app_user_user_id_seq', 1, true);

SELECT pg_catalog.setval('__SCHEMA__.member_member_id_seq', 5, true);
