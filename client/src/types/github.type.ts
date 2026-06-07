export type GithubRepo = {
    id: number;
    name: string;
    fullName: string;
    htmlUrl: string;
    cloneUrl:string;
    private: boolean;
    defaultBranch: string;
    description?: string | null;
    fork: boolean;
    owner: {
        login: string;
        avatarUrl: string;
        htmlUrl: string;
    };
};

export type GithubReposResponse = {
    repos: GithubRepo[];
};

export type GithubConnectResponse = {
    url: string;
};