import { Injectable, Logger } from '@nestjs/common';
import { createHash } from 'crypto';

import { DbService } from '../common/db.service';
import { CreateAlertDto } from './dto/create-alert.dto';

export interface IngestResult {
  id: number | null;
  status: 'accepted' | 'duplicate';
  fingerprint: string;
}

@Injectable()
export class AlertsService {
  private readonly logger = new Logger(AlertsService.name);
  private readonly dedupWindowSec = Number(process.env.DEDUP_WINDOW_SEC ?? 300);

  constructor(private readonly db: DbService) {}

  /**
   * Ingest is write-only and fast on purpose. Classification and correlation
   * happen out of band in the Python worker, so a burst of alerts never blocks
   * on an LLM call.
   */
  async ingest(dto: CreateAlertDto): Promise<IngestResult> {
    const fingerprint = dto.fingerprint ?? this.deriveFingerprint(dto);

    const duplicate = await this.db.queryOne<{ id: string }>(
      `SELECT id
         FROM alerts
        WHERE fingerprint = $1
          AND received_at > now() - ($2 || ' seconds')::interval
        LIMIT 1`,
      [fingerprint, this.dedupWindowSec],
    );

    if (duplicate) {
      this.logger.debug(`dropped duplicate alert fp=${fingerprint}`);
      return { id: Number(duplicate.id), status: 'duplicate', fingerprint };
    }

    const row = await this.db.queryOne<{ id: string }>(
      `INSERT INTO alerts (source, fingerprint, raw_payload, status)
       VALUES ($1, $2, $3::jsonb, 'new')
       RETURNING id`,
      [dto.source, fingerprint, JSON.stringify(dto.payload)],
    );

    return { id: Number(row.id), status: 'accepted', fingerprint };
  }

  async findOne(id: number) {
    return this.db.queryOne(
      `SELECT a.id, a.source, a.fingerprint, a.raw_payload, a.normalized,
              a.status, a.category, a.severity, a.classification_reason,
              a.received_at, a.processed_at,
              ai.incident_id
         FROM alerts a
         LEFT JOIN alert_incident ai ON ai.alert_id = a.id
        WHERE a.id = $1`,
      [id],
    );
  }

  async stats() {
    const [counts] = await this.db.query(
      `SELECT
         count(*)                                        AS total,
         count(*) FILTER (WHERE status = 'new')          AS pending,
         count(*) FILTER (WHERE status = 'correlated')   AS processed,
         count(*) FILTER (WHERE status = 'failed')       AS failed
       FROM alerts`,
    );
    return counts;
  }

  /**
   * A stable hash over source plus the fields most monitoring systems use to
   * identify "the same thing firing again".
   */
  private deriveFingerprint(dto: CreateAlertDto): string {
    const p = dto.payload as Record<string, any>;
    const parts = [
      dto.source,
      p.host ?? p.hostname ?? p.instance ?? '',
      p.service ?? p.app ?? '',
      p.check ?? p.rule ?? p.alertname ?? p.title ?? '',
    ];
    return createHash('sha1').update(parts.join('|')).digest('hex').slice(0, 32);
  }
}
