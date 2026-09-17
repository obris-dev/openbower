// The webhook delivery family: how a delivery reads (status word,
// tone, label, facts, time), the row every surface renders one with,
// the inline result panel, and the host a destination shows; shared by
// the settings pages and the sheet's Send webhook drawer. Route
// families reach for these through this index.
export { DeliveryResult } from "./delivery-result";
export { deliveryFacts, deliveryLabel, deliveryRead, deliveryWord, TONE_CLASS, type DeliveryTone } from "./lib/delivery-read";
export { formatTime } from "./lib/format-time";
export { hostOf } from "./lib/host-of";
export { DeliveryRow } from "./delivery-row";
