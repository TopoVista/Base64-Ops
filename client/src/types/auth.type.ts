export type LoginType = {
    email: string;
    password: string;
};

export type RegisterType = {
    email: string;
    password: string;
    name: string;
    avatar?: string;
};

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
