/**
 * Ambient typings for the `sqlite-vec` native extension (`MemoryBank` loads it via `node:sqlite`).
 */
declare module "sqlite-vec" {
  export function load(db: object): void;
}
