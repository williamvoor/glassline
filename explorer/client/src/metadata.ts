/** Small, independently parsed metadata fields; the A2A envelope remains forward compatible. */
import {z} from 'zod';

const optionalText = z.string().optional().catch(undefined);
export const ModelSchema = z.string().nullable().optional().catch(null);
export const TabSchema = z.object({id: optionalText, title: optionalText,
  kind: z.literal('board').optional().catch(undefined)}).passthrough().catch({});
export const LogSchema = z.object({text: z.string(), tag: optionalText}).passthrough().optional().catch(undefined);
export const BoardMetadataSchema = z.object({id: z.string(), controls: z.array(z.object({name: z.string(),
  kind: z.enum(['choice', 'multi', 'toggle', 'number', 'date'])}).passthrough()).optional().default([])}).passthrough();
export const InPlaceSchema = z.object({seq: z.number().int().nonnegative()}).passthrough();
