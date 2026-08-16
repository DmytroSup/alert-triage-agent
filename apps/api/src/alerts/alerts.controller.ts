import {
  Body,
  Controller,
  Get,
  HttpCode,
  NotFoundException,
  Param,
  ParseIntPipe,
  Post,
  UseGuards,
} from '@nestjs/common';

import { ApiKeyGuard } from '../common/api-key.guard';
import { AlertsService } from './alerts.service';
import { CreateAlertDto } from './dto/create-alert.dto';

@Controller('api/alerts')
export class AlertsController {
  constructor(private readonly alerts: AlertsService) {}

  @Post()
  @HttpCode(202)
  @UseGuards(ApiKeyGuard)
  async ingest(@Body() dto: CreateAlertDto) {
    const result = await this.alerts.ingest(dto);
    return {
      ...result,
      message:
        result.status === 'duplicate'
          ? 'alert matched an existing fingerprint inside the dedup window'
          : 'alert accepted, queued for classification',
    };
  }

  @Get('stats')
  stats() {
    return this.alerts.stats();
  }

  @Get(':id')
  async findOne(@Param('id', ParseIntPipe) id: number) {
    const alert = await this.alerts.findOne(id);
    if (!alert) throw new NotFoundException(`alert ${id} not found`);
    return alert;
  }
}
