export type AuthUser = {
  _id: string;
  name: string;
  email: string;
  avatar?: string | null;
  githubConnected?: boolean;
  createdAt?: string;
  updatedAt?: string;
};

export type AuthResponse = {
  message: string;
  user: AuthUser;
};
