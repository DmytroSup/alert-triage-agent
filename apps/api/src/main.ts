import 'reflect-metadata';
import { NestFactory } from '@nestjs/core';
import { Logger, ValidationPipe } from '@nestjs/common';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);

  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
    }),
  );

  // The dashboard is served from the same origin, so CORS is only needed when
  // someone points a separately hosted front end at this API.
  app.enableCors({ origin: process.env.CORS_ORIGIN ?? '*' });

  const port = Number(process.env.PORT ?? 3000);
  await app.listen(port, '0.0.0.0');

  new Logger('bootstrap').log(`API listening on http://localhost:${port}`);
}

bootstrap();
