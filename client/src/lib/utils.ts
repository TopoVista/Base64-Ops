import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import {customAlphabet} from "nanoid"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

const nanoid = customAlphabet(
  "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
  12
)

export function generateSlugId() {
 return nanoid()
}