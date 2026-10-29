# Etap 1: Budowanie aplikacji
FROM eclipse-temurin:25-jdk-alpine AS builder
WORKDIR /app

# Kopiujemy pliki wrappera i konfigurację Mavena, aby zcache'ować zależności
COPY mvnw .
COPY .mvn .mvn
COPY pom.xml .
RUN chmod +x mvnw && ./mvnw dependency:go-offline -B

# Kopiujemy kod źródłowy i budujemy paczkę (z pominięciem testów jednostkowych w tym kroku)
COPY src src
RUN ./mvnw clean package -DskipTests

# Etap 2: Obraz uruchomieniowy (lekki JRE)
FROM eclipse-temurin:25-jre-alpine
WORKDIR /app

# Uruchamianie z uprawnieniami zwykłego użytkownika (bezpieczeństwo)
RUN addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser

COPY --from=builder /app/target/*-SNAPSHOT.jar app.jar

ENV SPRING_PROFILES_ACTIVE=dev

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]