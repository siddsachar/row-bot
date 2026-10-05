/**
 * The first import of every page entry point, so `crypto.randomUUID` exists
 * before any other module runs, including on a plain-HTTP network page.
 */
import { provideRandomUUID } from './crypto';

provideRandomUUID();
