// The webhook delivery family: how a delivery reads (status word,
// tone, label, time) and the inline result panel, shared by the
// settings pages and the sheet's Send webhook drawer. Route families
// reach for these through this index.
export { DeliveryResult } from "./delivery-result";
export { deliveryLabel, deliveryRead, deliveryWord, TONE_CLASS, type DeliveryTone } from "./lib/delivery-read";
export { formatTime } from "./lib/format-time";
export { hostOf } from "./lib/host-of";
