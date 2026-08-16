import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { ServeStaticModule } from '@nestjs/serve-static';
import { join } from 'path';

import { DbModule } from './common/db.module';
import { AlertsModule } from './alerts/alerts.module';
import { IncidentsModule } from './incidents/incidents.module';

@Module({
  imports: [
    ConfigModule.forRoot({ isGlobal: true }),
    // Single-page dashboard. Keeping it inside the API removes the need for a
    // second host in the default setup.
    ServeStaticModule.forRoot({
      rootPath: join(__dirname, 'public'),
      serveRoot: '/',
      exclude: ['/api/{*splat}'],
    }),
    DbModule,
    AlertsModule,
    IncidentsModule,
  ],
})
export class AppModule {}
