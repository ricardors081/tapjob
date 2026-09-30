// Typed access to the canonical product identity. Validation lives in the backend loader
// (tapjob_product_config), which CI runs against the same product.json.
import product from "../product.json";

export type ProductConfig = typeof product;

export const productConfig: Readonly<ProductConfig> = Object.freeze(product);
