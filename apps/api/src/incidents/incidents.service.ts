import { Injectable, NotFoundException } from '@nestjs/common';

import { DbService } from '../common/db.service';
import { ListIncidentsQueryDto } from '../alerts/dto/create-alert.dto';

@Injectable()
export class IncidentsService {
  constructor(private readonly db: DbService) {}

  async findAll(q: ListIncidentsQueryDto) {
    const where: string[] = [];
    const params: unknown[] = [];

    if (q.status) {
      params.push(q.status);
      where.push(`i.status = $${params.length}::incident_status`);
    }
    if (q.severity) {
      params.push(q.severity);
      where.push(`i.severity = $${params.length}::severity_level`);
    }
    if (q.category) {
      params.push(q.category);
      where.push(`i.category = $${params.length}::alert_category`);
    }

    params.push(Math.min(Number(q.limit ?? 50), 200));

    return this.db.query(
      `SELECT i.id, i.title, i.summary, i.category, i.severity, i.status,
              i.alert_count, i.created_at, i.updated_at,
              array_remove(array_agg(DISTINCT a.source), NULL) AS sources
         FROM incidents i
         LEFT JOIN alert_incident ai ON ai.incident_id = i.id
         LEFT JOIN alerts a          ON a.id = ai.alert_id
        ${where.length ? 'WHERE ' + where.join(' AND ') : ''}
        GROUP BY i.id
        ORDER BY
          CASE i.severity WHEN 'P1' THEN 1 WHEN 'P2' THEN 2
                          WHEN 'P3' THEN 3 ELSE 4 END,
          i.created_at DESC
        LIMIT $${params.length}`,
      params,
    );
  }

  async findOne(id: number) {
    const incident = await this.db.queryOne(
      `SELECT id, title, summary, category, severity, status,
              alert_count, created_at, updated_at, closed_at
         FROM incidents
        WHERE id = $1`,
      [id],
    );
    if (!incident) throw new NotFoundException(`incident ${id} not found`);

    const alerts = await this.db.query(
      `SELECT a.id, a.source, a.category, a.severity,
              a.classification_reason, a.normalized, a.received_at
         FROM alerts a
         JOIN alert_incident ai ON ai.alert_id = a.id
        WHERE ai.incident_id = $1
        ORDER BY a.received_at ASC`,
      [id],
    );

    return { ...incident, alerts };
  }

  async setStatus(id: number, status: 'open' | 'acknowledged' | 'closed') {
    const row = await this.db.queryOne(
      `UPDATE incidents
          SET status = $2::incident_status,
              closed_at = CASE WHEN $2 = 'closed' THEN now() ELSE NULL END
        WHERE id = $1
        RETURNING id, status, closed_at`,
      [id, status],
    );
    if (!row) throw new NotFoundException(`incident ${id} not found`);
    return row;
  }

  /** Numbers the dashboard header shows. */
  async summary() {
    const [row] = await this.db.query(
      `SELECT
         count(*)                                      AS total,
         count(*) FILTER (WHERE status = 'open')       AS open,
         count(*) FILTER (WHERE severity = 'P1')       AS p1,
         count(*) FILTER (WHERE severity = 'P2')       AS p2,
         COALESCE(sum(alert_count), 0)                 AS alerts_grouped
       FROM incidents`,
    );
    return row;
  }
}
