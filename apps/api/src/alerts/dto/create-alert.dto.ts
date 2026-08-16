import { Type } from 'class-transformer';
import {
  IsIn,
  IsNotEmpty,
  IsObject,
  IsOptional,
  IsString,
  MaxLength,
} from 'class-validator';

/**
 * The ingestion contract is deliberately loose: every monitoring system emits a
 * different shape, so only `source` and `payload` are required. Normalisation
 * into a common schema is the worker's job, not the caller's.
 */
export class CreateAlertDto {
  @IsString()
  @IsNotEmpty()
  @MaxLength(120)
  source: string;

  @IsObject()
  payload: Record<string, unknown>;

  /**
   * Optional caller-supplied dedup key. When two alerts arrive with the same
   * fingerprint inside the dedup window, the second one is dropped.
   */
  @IsOptional()
  @IsString()
  @MaxLength(200)
  fingerprint?: string;
}

export class ListIncidentsQueryDto {
  @IsOptional()
  @IsIn(['open', 'acknowledged', 'closed'])
  status?: string;

  @IsOptional()
  @IsIn(['P1', 'P2', 'P3', 'P4'])
  severity?: string;

  @IsOptional()
  @IsIn(['infra', 'security', 'application', 'network', 'unknown'])
  category?: string;

  @IsOptional()
  @Type(() => Number)
  limit?: number;
}
