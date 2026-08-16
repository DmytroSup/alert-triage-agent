import {
  CanActivate,
  ExecutionContext,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import { Request } from 'express';

/**
 * Ingestion is protected by a shared key sent as `x-api-key`.
 *
 * This is intentionally the simplest thing that is still honest: alert sources
 * are machines, not people, so there is no user model and no session to manage.
 * If INGEST_API_KEY is unset the guard stays open, which keeps `docker compose
 * up` usable out of the box.
 */
@Injectable()
export class ApiKeyGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    const expected = process.env.INGEST_API_KEY;
    if (!expected) return true;

    const req = context.switchToHttp().getRequest<Request>();
    const provided = req.header('x-api-key');

    if (provided !== expected) {
      throw new UnauthorizedException('invalid or missing x-api-key');
    }
    return true;
  }
}
