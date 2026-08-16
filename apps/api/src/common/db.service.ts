import {
  Injectable,
  Logger,
  OnModuleDestroy,
  OnModuleInit,
} from '@nestjs/common';
import { Pool, PoolClient, QueryResultRow } from 'pg';

/**
 * Thin wrapper over a pg connection pool.
 *
 * Deliberately no ORM: the interesting parts of this project are the JOIN that
 * builds an incident view and the transaction that links an alert to an
 * incident. An ORM would hide both behind generated SQL.
 */
@Injectable()
export class DbService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(DbService.name);
  private pool: Pool;

  onModuleInit() {
    this.pool = new Pool({
      connectionString:
        process.env.DATABASE_URL ??
        'postgres://triage:triage@localhost:5432/triage',
      max: Number(process.env.PG_POOL_MAX ?? 10),
      idleTimeoutMillis: 30_000,
    });

    this.pool.on('error', (err) =>
      this.logger.error(`idle client error: ${err.message}`),
    );
  }

  async onModuleDestroy() {
    await this.pool?.end();
  }

  async query<T extends QueryResultRow = any>(
    text: string,
    params: unknown[] = [],
  ): Promise<T[]> {
    const res = await this.pool.query<T>(text, params);
    return res.rows;
  }

  async queryOne<T extends QueryResultRow = any>(
    text: string,
    params: unknown[] = [],
  ): Promise<T | null> {
    const rows = await this.query<T>(text, params);
    return rows[0] ?? null;
  }

  /** Runs `fn` inside a transaction, rolling back on any thrown error. */
  async transaction<T>(fn: (client: PoolClient) => Promise<T>): Promise<T> {
    const client = await this.pool.connect();
    try {
      await client.query('BEGIN');
      const result = await fn(client);
      await client.query('COMMIT');
      return result;
    } catch (err) {
      await client.query('ROLLBACK');
      throw err;
    } finally {
      client.release();
    }
  }

  async healthy(): Promise<boolean> {
    try {
      await this.pool.query('SELECT 1');
      return true;
    } catch {
      return false;
    }
  }
}
