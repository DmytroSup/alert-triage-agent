import {
  Body,
  Controller,
  Get,
  Param,
  ParseIntPipe,
  Patch,
  Query,
} from '@nestjs/common';

import { ListIncidentsQueryDto } from '../alerts/dto/create-alert.dto';
import { IncidentsService } from './incidents.service';

@Controller('api/incidents')
export class IncidentsController {
  constructor(private readonly incidents: IncidentsService) {}

  @Get()
  findAll(@Query() query: ListIncidentsQueryDto) {
    return this.incidents.findAll(query);
  }

  @Get('summary')
  summary() {
    return this.incidents.summary();
  }

  @Get(':id')
  findOne(@Param('id', ParseIntPipe) id: number) {
    return this.incidents.findOne(id);
  }

  @Patch(':id/status')
  setStatus(
    @Param('id', ParseIntPipe) id: number,
    @Body('status') status: 'open' | 'acknowledged' | 'closed',
  ) {
    return this.incidents.setStatus(id, status);
  }
}
