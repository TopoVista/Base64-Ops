import { z } from "zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { loginMutationFn } from "@/lib/api";
import type { LoginType } from "@/types/auth.type";
import Logo from "@/components/logo";
import {PROTECTED_ROUTES} from "@/routes/route"

const loginSchema = z.object({
  email: z.string().trim().email("Enter a valid email address"),
  password: z.string().trim().min(1, "Password is required"),
});

const SignInPage = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const loginMutation = useMutation({
    mutationFn: loginMutationFn,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["current-user"] });
      navigate(PROTECTED_ROUTES.NEW);
    },
    onError: (error: any) => {
      toast.error(
        error?.response?.data?.message ||
          error?.message ||
          "Unable to login. Try again."
      );
    },
  });

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginType>({
    resolver: zodResolver(loginSchema),
    defaultValues: {
      email: "",
      password: "",
    },
  });

  const onSubmit = async (values: LoginType) => {
    loginMutation.mutate(values);
  };

  return (
    <div className="grid min-h-svh lg:grid-cols-2">
      <div className="flex flex-col justify-between gap-10 p-6 md:p-10">
        <Logo />

        <div className="mx-auto flex w-full max-w-sm flex-1 items-center">
          <form onSubmit={handleSubmit(onSubmit)} className="w-full space-y-6">
            <div className="space-y-2">
              <h1 className="text-3xl font-semibold tracking-tighter">
                Sign in
              </h1>
              <p className="text-sm text-muted-foreground">
                Use your account to continue to your coding sessions.
              </p>
            </div>

            <div className="space-y-4">
              <div className="space-y-2">
                <label htmlFor="email" className="text-sm font-medium">
                  Email
                </label>
                <Input
                  id="email"
                  type="email"
                  placeholder="name@example.com"
                  autoComplete="email"
                  {...register("email")}
                />
                {errors.email ? (
                  <p className="text-sm text-destructive">{errors.email.message}</p>
                ) : null}
              </div>

              <div className="space-y-2">
                <label htmlFor="password" className="text-sm font-medium">
                  Password
                </label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  {...register("password")}
                />
                {errors.password ? (
                  <p className="text-sm text-destructive">
                    {errors.password.message}
                  </p>
                ) : null}
              </div>
            </div>

            <Button type="submit" className="w-full" disabled={loginMutation.isPending}>
              {loginMutation.isPending ? "Signing in..." : "Sign in"}
            </Button>

            <p className="text-sm text-muted-foreground">
              Don&apos;t have an account?{" "}
              <Link to="/sign-up" className="font-medium text-foreground underline underline-offset-4">
                Sign up
              </Link>
            </p>
          </form>
        </div>
      </div>

      <div className="relative hidden overflow-hidden border-l bg-gradient-to-br from-zinc-950 via-zinc-900 to-zinc-800 lg:block">
        <div className="absolute inset-0 opacity-50 [background-image:radial-gradient(circle_at_20%_20%,rgba(255,255,255,0.16),transparent_30%),radial-gradient(circle_at_80%_10%,rgba(255,255,255,0.12),transparent_22%),radial-gradient(circle_at_50%_80%,rgba(255,255,255,0.08),transparent_28%)]" />
        <div className="relative flex h-full flex-col justify-end p-10 text-white">
          <p className="max-w-md text-3xl font-semibold leading-tight">
            Connect GitHub, spin up a workspace, and let the agent build.
          </p>
          <p className="mt-4 max-w-md text-sm text-white/70">
            One session, one branch, one sandbox.
          </p>
        </div>
      </div>
    </div>
  );
};

export default SignInPage;
