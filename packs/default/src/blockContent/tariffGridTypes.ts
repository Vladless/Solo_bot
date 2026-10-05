export type TariffFeature = {
  label?: string;
  value?: string;
};

export type TariffItem = {
  code?: string;
  name?: string;
  desc?: string;
  badge?: string;
  highlighted?: boolean;
  price?: string;
  currency?: string;
  period?: string;
  features?: TariffFeature[];
  ctaLabel?: string;
  ctaHref?: string;
  tariffId?: number;
};
