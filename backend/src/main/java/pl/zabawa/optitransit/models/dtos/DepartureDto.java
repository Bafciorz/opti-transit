package pl.zabawa.optitransit.models.dtos;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class DepartureDto {

    private String line;
    private String headsign;
    private Integer routeType;
    private String routeColor;
    private String routeTextColor;
    private String departureTime;
    private Long minutesToDeparture;
    private Boolean wheelchairAccessible;
    private String tripId;
    private String stopId;
}
