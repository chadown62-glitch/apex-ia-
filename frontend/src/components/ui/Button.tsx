import type { ButtonHTMLAttributes } from "react";
import "./ui.css";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export function Button({ variant = "secondary", size = "md", className = "", type = "button", ...rest }: ButtonProps) {
  const classes = ["btn", `btn--${variant}`, size === "sm" ? "btn--sm" : "", className].filter(Boolean).join(" ");
  return <button type={type} className={classes} {...rest} />;
}

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean;
  "aria-label": string;
}

export function IconButton({ active, className = "", type = "button", ...rest }: IconButtonProps) {
  const classes = ["icon-btn", active ? "icon-btn--active" : "", className].filter(Boolean).join(" ");
  return <button type={type} className={classes} {...rest} />;
}
