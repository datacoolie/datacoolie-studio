import logoSrc from "../assets/datacoolie-icon-circle-dark_512x512.png";

export function BrandLogo() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <img src={logoSrc} alt="" />
    </div>
  );
}
